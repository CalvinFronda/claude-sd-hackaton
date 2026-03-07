"""
San Diego City Council — Playwright Scraper
============================================
Discovers all meetings, extracts agenda items via browser automation,
downloads the public-comment Excel files, parses comment rows, and
writes everything into a SQLite database with comments linked to
the correct agenda item.

Usage:
    # Install deps once:
    pip install playwright pandas openpyxl requests
    playwright install chromium

    # Test run (10 meetings, shows browser):
    python scraper.py --output ../data --limit 10 --no-headless

    # Full historical run:
    python scraper.py --output ../data --start 2024-01-01
"""

import asyncio
import re
import sqlite3
import logging
import argparse
import json
import time
import requests
from pathlib import Path
from datetime import datetime
from urllib.parse import urlparse, parse_qs, urljoin
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

BASE_URL   = "https://sandiego.hylandcloud.com/211agendaonlinecouncil"
EXCEL_BASE = "https://www.sandiego.gov/sites/default/files"

# ─────────────────────────────────────────────────────────────────────────────
# Excel URL derivation
# ─────────────────────────────────────────────────────────────────────────────

def derive_excel_url(date_str: str) -> str | None:
    """
    Construct the predictable sandiego.gov Excel URL from a meeting date.
    Date format expected: "YYYY-MM-DD"

    Resulting URL pattern:
    https://www.sandiego.gov/sites/default/files/YYYY-MM/MM-DD-YYYY-public-comments-submitted-via-webform.xlsx
    """
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        folder  = dt.strftime("%Y-%m")          # 2026-03
        file    = dt.strftime("%m-%d-%Y")       # 03-03-2026
        return f"{EXCEL_BASE}/{folder}/{file}-public-comments-submitted-via-webform.xlsx"
    except Exception:
        return None


def try_download_excel(url: str, dest: Path) -> bool:
    """Directly download the Excel file from sandiego.gov (no auth needed)."""
    try:
        r = requests.get(url, timeout=30, stream=True,
                         headers={"User-Agent": "Mozilla/5.0 (Research)"})
        if r.status_code == 200:
            dest.parent.mkdir(parents=True, exist_ok=True)
            with open(dest, "wb") as f:
                for chunk in r.iter_content(8192):
                    f.write(chunk)
            log.info(f"    ✓ Downloaded {dest.name} ({dest.stat().st_size // 1024} KB)")
            return True
        else:
            log.warning(f"    HTTP {r.status_code} for {url}")
            return False
    except Exception as e:
        log.error(f"    Download error: {e}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Item-number normalisation  (for fuzzy matching comments → agenda items)
# ─────────────────────────────────────────────────────────────────────────────

# Patterns seen in SD agendas:  100, 200, 200A, B-100, S-1, S500, NON-AGENDA
_ITEM_RE = re.compile(r'\b([A-Z][-\s]?)?\d{1,4}[A-Z]?\b', re.IGNORECASE)

def normalise_item(raw: str) -> str:
    """
    Strip punctuation/spaces and lowercase for comparison.
    '200 A'  → '200a'
    'Item B-100' → 'b-100'
    'S-1'  → 's-1'
    '#100' → '100'
    """
    if not raw:
        return ""
    s = str(raw).strip().lower()
    s = re.sub(r'^(item\s*|#\s*)', '', s)   # remove leading "item" or "#"
    s = re.sub(r'\s+', '', s)               # collapse whitespace
    return s


def extract_item_numbers(text: str) -> list[str]:
    """
    Extract all item-number tokens from free text.
    Handles comma/semicolon-separated lists: "100, 200A, and 300"
    """
    if not text:
        return []
    # Split on common delimiters first
    parts = re.split(r'[,;&/\n]+', str(text))
    nums = []
    for part in parts:
        part = part.strip()
        m = _ITEM_RE.search(part)
        if m:
            nums.append(normalise_item(m.group()))
    return list(dict.fromkeys(nums))  # deduplicate, preserve order


# ─────────────────────────────────────────────────────────────────────────────
# Excel comment parsing
# ─────────────────────────────────────────────────────────────────────────────

# Map of our canonical field name → possible column header spellings
COLUMN_MAP = {
    "commenter_name": ["name", "full name", "commenter", "commenter name", "submitter name"],
    "email":          ["email", "e-mail", "email address"],
    "phone":          ["phone", "phone number", "telephone"],
    "address":        ["address", "street address", "mailing address"],
    "district":       ["district", "council district", "cd"],
    "raw_item_ref":   [
        "agenda item", "item", "item number", "item no", "item #",
        "agenda item number", "items", "item(s)", "item numbers",
        "which agenda item", "agenda item(s)"
    ],
    "position":       [
        "oppose / favor", "oppose/favor", "oppose", "favor",
        "position", "stance", "support/oppose", "for/against",
        "i am", "i would like to", "comment category"
    ],
    "comment_text":   [
        "comment:", "comment", "comments", "comment text", "message", "remarks",
        "testimony", "written comment", "public comment", "your comment"
    ],
    "submitted_at":   ["date", "submitted", "date submitted", "timestamp", "time", "submission date"],
}


def _best_col_match(df_cols: list[str], aliases: list[str]) -> str | None:
    """Return the first df column whose normalised name matches any alias."""
    lookup = {c.strip().lower(): c for c in df_cols}
    for alias in aliases:
        if alias in lookup:
            return lookup[alias]
    # Partial match fallback
    for alias in aliases:
        for col_lower, col_orig in lookup.items():
            if alias in col_lower or col_lower in alias:
                return col_orig
    return None


def parse_excel_comments(filepath: Path, meeting_id: int, meeting_date: str) -> list[dict]:
    """
    Parse one comment Excel file.
    Returns a list of dicts ready for DB insertion (raw_item_ref still as string).
    """
    records = []
    try:
        xl = pd.ExcelFile(filepath)
        for sheet_name in xl.sheet_names:
            df = xl.parse(sheet_name, dtype=str, header=None)
            df = df.dropna(how="all").reset_index(drop=True)
            if df.empty:
                continue

            # ── Detect header row ──────────────────────────────────────────
            header_row = 0
            for i, row in df.iterrows():
                vals = [str(v).strip().lower() for v in row.values if pd.notna(v)]
                # A row is a header if it contains ≥2 recognised column aliases
                hits = sum(
                    1 for v in vals
                    if any(v == alias or alias in v
                           for aliases in COLUMN_MAP.values() for alias in aliases)
                )
                if hits >= 2:
                    header_row = i
                    break

            df.columns = df.iloc[header_row].str.strip()
            df = df.iloc[header_row + 1:].reset_index(drop=True)
            df = df.dropna(how="all")
            if df.empty:
                continue

            # ── Map column names ───────────────────────────────────────────
            col_lookup = {}
            for canonical, aliases in COLUMN_MAP.items():
                matched = _best_col_match(list(df.columns), aliases)
                if matched:
                    col_lookup[canonical] = matched

            comment_col = col_lookup.get("comment_text")
            if not comment_col:
                log.warning(f"    No comment column found in sheet '{sheet_name}' of {filepath.name}")
                continue

            for _, row in df.iterrows():
                comment_text = str(row.get(comment_col, "")).strip()
                if not comment_text or comment_text.lower() in ("nan", "none", ""):
                    continue

                def g(canonical):
                    col = col_lookup.get(canonical)
                    return str(row[col]).strip() if col and col in row.index else ""

                records.append({
                    "meeting_id":     meeting_id,
                    "meeting_date":   meeting_date,
                    "commenter_name": g("commenter_name"),
                    "email":          g("email"),
                    "phone":          g("phone"),
                    "address":        g("address"),
                    "district":       g("district"),
                    "raw_item_ref":   g("raw_item_ref"),
                    "position":       g("position"),
                    "comment_text":   comment_text,
                    "submitted_at":   g("submitted_at"),
                    "source_file":    filepath.name,
                    "agenda_item_id": None,   # resolved in a second pass
                })

    except Exception as e:
        log.error(f"    Excel parse error ({filepath.name}): {e}", exc_info=True)

    return records


# ─────────────────────────────────────────────────────────────────────────────
# Comment → Agenda item mapping
# ─────────────────────────────────────────────────────────────────────────────

def build_item_index(conn: sqlite3.Connection, meeting_id: int) -> dict[str, int]:
    """
    Returns {normalised_item_number: agenda_item.id} for one meeting.
    """
    cur = conn.execute(
        "SELECT id, item_number_norm FROM agenda_items WHERE meeting_id = ?",
        (meeting_id,)
    )
    return {row[1]: row[0] for row in cur.fetchall()}


def resolve_comment_items(conn: sqlite3.Connection):
    """
    Walk every unresolved comment, parse its raw_item_ref, and
    set agenda_item_id where a match exists in agenda_items.
    Handles:
      - single items:   "100"
      - alphanumeric:   "200A", "S-1", "B-100"
      - lists:          "100, 200A, and 300"  → links to first recognised item
      - non-agenda:     "Non-Agenda", "General" → stays NULL
    """
    cur = conn.execute(
        """SELECT id, meeting_id, raw_item_ref
           FROM public_comments
           WHERE agenda_item_id IS NULL AND raw_item_ref != ''"""
    )
    rows = cur.fetchall()
    log.info(f"  Resolving item references for {len(rows)} comments…")

    # Cache item indexes per meeting
    item_indexes: dict[int, dict[str, int]] = {}
    updated = 0

    for comment_id, meeting_id, raw_ref in rows:
        if meeting_id not in item_indexes:
            item_indexes[meeting_id] = build_item_index(conn, meeting_id)

        idx = item_indexes[meeting_id]
        if not idx:
            continue

        candidates = extract_item_numbers(raw_ref)
        matched_id = None
        for cand in candidates:
            if cand in idx:
                matched_id = idx[cand]
                break
            # Fuzzy 1: strip leading letter from candidate ("s400" → "400")
            stripped = re.sub(r'^[a-z]-?', '', cand)
            if stripped and stripped in idx:
                matched_id = idx[stripped]
                break
            # Fuzzy 2: strip leading letter from index keys to match bare number
            # e.g. candidate "400" matches index key "s400"
            for key, key_id in idx.items():
                key_stripped = re.sub(r'^[a-z]-?', '', key)
                if key_stripped == cand:
                    matched_id = key_id
                    break
            if matched_id:
                break

        if matched_id:
            conn.execute(
                "UPDATE public_comments SET agenda_item_id = ? WHERE id = ?",
                (matched_id, comment_id)
            )
            updated += 1

    conn.commit()
    log.info(f"  Resolved {updated}/{len(rows)} comment→item links.")

    # Report unmatched
    cur = conn.execute(
        """SELECT raw_item_ref, COUNT(*) as n
           FROM public_comments
           WHERE agenda_item_id IS NULL AND raw_item_ref != ''
             AND raw_item_ref NOT LIKE '%non%agenda%'
             AND raw_item_ref NOT LIKE '%general%'
           GROUP BY raw_item_ref ORDER BY n DESC LIMIT 20"""
    )
    unmatched = cur.fetchall()
    if unmatched:
        log.warning("  Top unmatched item references (may need manual review):")
        for ref, n in unmatched:
            log.warning(f"    '{ref}' — {n} comments")


# ─────────────────────────────────────────────────────────────────────────────
# Database helpers
# ─────────────────────────────────────────────────────────────────────────────

def init_db(db_path: Path) -> sqlite3.Connection:
    schema = (Path(__file__).parent / "schema.sql").read_text()
    conn = sqlite3.connect(db_path)
    conn.executescript(schema)
    conn.commit()
    return conn


def save_meeting(conn: sqlite3.Connection, m: dict):
    conn.execute(
        """INSERT OR IGNORE INTO meetings
           (meeting_id, title, date, day_of_week, meeting_type, agenda_url, excel_url, scraped_at)
           VALUES (:meeting_id, :title, :date, :day_of_week, :meeting_type, :agenda_url, :excel_url, :scraped_at)""",
        m
    )
    conn.commit()


def save_agenda_items(conn: sqlite3.Connection, items: list[dict]):
    # Delete old items for this meeting first (idempotent re-scrape)
    if items:
        conn.execute("DELETE FROM agenda_items WHERE meeting_id = ?", (items[0]["meeting_id"],))
    conn.executemany(
        """INSERT INTO agenda_items
           (meeting_id, item_number, item_number_norm, title, description, category, district)
           VALUES (:meeting_id, :item_number, :item_number_norm, :title, :description, :category, :district)""",
        items
    )
    conn.commit()


def save_comments(conn: sqlite3.Connection, comments: list[dict]):
    conn.executemany(
        """INSERT INTO public_comments
           (meeting_id, meeting_date, commenter_name, email, phone, address,
            district, raw_item_ref, position, comment_text, submitted_at, source_file)
           VALUES (:meeting_id, :meeting_date, :commenter_name, :email, :phone, :address,
                   :district, :raw_item_ref, :position, :comment_text, :submitted_at, :source_file)""",
        comments
    )
    conn.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Playwright — meeting list + agenda item scraping
# ─────────────────────────────────────────────────────────────────────────────

async def scrape_meeting_list(page, start_date: str | None, limit: int | None) -> list[dict]:
    """Navigate the OnBase Meetings search page and collect all agenda meetings."""
    log.info("Loading meeting list…")
    # Use DateRangeOptionID=2 (Last Year) to get a full year of meetings
    await page.goto(f"{BASE_URL}/Meetings/Search?site=council&DateRangeOptionID=2",
                    wait_until="networkidle", timeout=30000)

    meetings = []
    seen_ids = set()
    start_dt = datetime.strptime(start_date, "%Y-%m-%d") if start_date else None

    while True:
        # Only keep links whose visible text is exactly "Agenda"
        anchors = await page.query_selector_all("a[href*='ViewMeeting']")
        for anchor in anchors:
            link_text = (await anchor.inner_text()).strip()
            if link_text != "Agenda":
                continue

            href = await anchor.get_attribute("href") or ""
            qs   = parse_qs(urlparse(href).query)
            mid  = qs.get("id", [None])[0]
            if not mid or mid in seen_ids:
                continue

            # Skip Closed Session agendas
            skip_keywords = ["closed session", "adjournment", "memo", "authority"]

            # Get meeting title + date from parent container text
            parent_text = await anchor.evaluate(
                "el => { let p = el; for (let i=0;i<5;i++) p=p.parentElement; return p ? p.innerText : ''; }"
            )
            lines = [l.strip() for l in parent_text.splitlines() if l.strip()]

            # First line is usually the date (M/D/YYYY)
            date_str = None
            title    = ""
            for line in lines:
                for fmt in ("%m/%d/%Y", "%m-%d-%Y"):
                    try:
                        dt = datetime.strptime(line, fmt)
                        date_str = dt.strftime("%Y-%m-%d")
                        break
                    except ValueError:
                        pass
                if date_str:
                    break
            # Title is the line that isn't a date/doc-type word
            doc_words = {"agenda", "summary", "public comment"}
            for line in lines:
                if line.lower() not in doc_words and not re.match(r'\d+/\d+/\d+', line):
                    title = line
                    break

            if any(kw in title.lower() for kw in skip_keywords):
                continue

            seen_ids.add(mid)

            # Apply date filter
            if start_dt and date_str:
                try:
                    if datetime.strptime(date_str, "%Y-%m-%d") < start_dt:
                        continue
                except ValueError:
                    pass

            t_lower = title.lower()
            mtype = "Special" if "special" in t_lower else "Regular"
            dow   = next((d for d in ["Monday","Tuesday","Wednesday","Thursday","Friday"]
                          if d.lower() in t_lower), None)

            meetings.append({
                "meeting_id":   int(mid),
                "title":        title,
                "date":         date_str or "",
                "day_of_week":  dow,
                "meeting_type": mtype,
                "agenda_url":   urljoin(BASE_URL, href),
                "excel_url":    derive_excel_url(date_str) if date_str else None,
                "scraped_at":   datetime.utcnow().isoformat(),
            })

        # Pagination — look for a "Next" button
        next_btn = await page.query_selector("a:has-text('Next'), a:has-text('›'), .pagination-next")
        if next_btn:
            await next_btn.click()
            await page.wait_for_load_state("networkidle", timeout=15000)
        else:
            break

        if limit and len(meetings) >= limit:
            break

    if limit:
        meetings = meetings[:limit]

    log.info(f"Found {len(meetings)} agenda meetings.")
    return meetings


async def scrape_agenda_items(page, meeting: dict) -> list[dict]:
    """
    Navigate to the ViewAgenda document page and extract all numbered items.

    The agenda is rendered as a Word-to-HTML document. Numbered items appear
    in 2-column <tr> rows: first <td> has bold "Item 200:" text, second <td>
    has the title via a loadAgendaItem() link.
    """
    items = []
    # The agenda content lives on the ViewAgenda document endpoint, not the
    # ViewMeeting page. Derive it from the agenda_url meeting ID.
    meeting_id = meeting["meeting_id"]
    doc_url = f"{BASE_URL}/Documents/ViewAgenda?meetingId={meeting_id}&doctype=1"

    try:
        await page.goto(doc_url, wait_until="networkidle", timeout=30000)
        # Wait for the document table to render
        await page.wait_for_selector("table", timeout=10000)
    except Exception:
        log.warning(f"  ViewAgenda did not load for meeting {meeting_id}")
        return items

    # ── Extract rows with "Item NNN:" in first <td> ───────────────────────
    raw_items = await page.evaluate(r"""() => {
        const results = [];
        const itemRe = /^Item\s+([A-Z]?-?\d+[A-Za-z]*)\s*:/i;
        document.querySelectorAll("tr").forEach(tr => {
            const tds = tr.querySelectorAll("td");
            if (tds.length < 2) return;
            const numText = tds[0].innerText.trim();
            const m = itemRe.exec(numText);
            if (!m) return;
            const itemNum = m[1].trim();
            // Title comes from the loadAgendaItem link in the second td
            const link = tds[1].querySelector("a[href*='loadAgendaItem']");
            const title = link ? link.innerText.trim() : tds[1].innerText.trim().split("\n")[0].trim();
            const fullText = tds[1].innerText.trim();
            results.push({ itemNum, title, fullText });
        });
        return results;
    }""")

    for r in raw_items:
        item_num  = r["itemNum"].strip()
        title     = r["title"][:200]
        full_text = r["fullText"]
        items.append({
            "meeting_id":       meeting_id,
            "item_number":      item_num,
            "item_number_norm": normalise_item(item_num),
            "title":            title,
            "description":      full_text[:1000] if len(full_text) > 200 else "",
            "category":         "",
            "district":         _extract_district(full_text),
        })

    log.info(f"  Extracted {len(items)} agenda items for meeting {meeting_id}")
    return items


def parse_agenda_from_text(text: str, meeting_id: int) -> list[dict]:
    """
    Fallback: scan raw text for lines that start with an item number pattern.
    Handles:  100  |  200A  |  B-100  |  S-1  |  S500
    """
    items = []
    current_category = ""
    lines = text.splitlines()

    ITEM_LINE = re.compile(
        r'^(?P<num>[A-Z][-\s]?\d{1,4}[A-Za-z]?|\d{1,4}[A-Za-z]?)'
        r'[\s.\-:]+(?P<title>.{5,200})$',
        re.IGNORECASE
    )
    SECTION_RE = re.compile(r'^[A-Z\s]{4,50}:?\s*$')

    for line in lines:
        line = line.strip()
        if not line:
            continue

        if SECTION_RE.match(line) and len(line) < 60:
            current_category = line
            continue

        m = ITEM_LINE.match(line)
        if m:
            item_num = m.group("num").strip()
            title    = m.group("title").strip()
            items.append({
                "meeting_id":       meeting_id,
                "item_number":      item_num,
                "item_number_norm": normalise_item(item_num),
                "title":            title[:200],
                "description":      "",
                "category":         current_category,
                "district":         _extract_district(line),
            })

    return items


def _extract_district(text: str) -> str:
    """Extract council district mention from text, e.g. 'District 3'."""
    m = re.search(r'district\s+(\d)', text, re.IGNORECASE)
    return m.group(1) if m else ""


# ─────────────────────────────────────────────────────────────────────────────
# Main orchestration
# ─────────────────────────────────────────────────────────────────────────────

async def run_async(output_dir: str, start_date: str | None,
                    limit: int | None, headless: bool):
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        raise SystemExit("Install playwright: pip install playwright && playwright install chromium")

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    excel_dir = out / "excel_files"
    excel_dir.mkdir(exist_ok=True)
    db_path = out / "council.db"

    log.info(f"Database : {db_path}")
    conn = init_db(db_path)

    stats = {"meetings": 0, "items": 0, "comments": 0, "errors": []}

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=headless)
        ctx     = await browser.new_context(accept_downloads=True)
        page    = await ctx.new_page()

        # ── Step 1: Discover all meetings ──────────────────────────────────
        meetings = await scrape_meeting_list(page, start_date, limit)
        for m in meetings:
            save_meeting(conn, m)
        stats["meetings"] = len(meetings)
        log.info(f"Saved {len(meetings)} meetings to DB.")

        # ── Step 2: Per meeting — scrape agenda items + download Excel ─────
        for i, meeting in enumerate(meetings, 1):
            log.info(f"\n[{i}/{len(meetings)}] {meeting['title']} ({meeting['date']})")

            # 2a. Agenda items
            try:
                items = await scrape_agenda_items(page, meeting)
                if items:
                    save_agenda_items(conn, items)
                    stats["items"] += len(items)
            except Exception as e:
                msg = f"Agenda scrape failed for {meeting['meeting_id']}: {e}"
                log.error(msg)
                stats["errors"].append(msg)

            # 2b. Excel comments — try derived URL first (fastest)
            excel_path = excel_dir / f"comments_{meeting['meeting_id']}.xlsx"
            downloaded = False

            if not excel_path.exists():
                if meeting.get("excel_url"):
                    log.info(f"  Trying derived URL: {meeting['excel_url']}")
                    downloaded = try_download_excel(meeting["excel_url"], excel_path)

                # Fallback: parse the "Click Here to View Comments" href on the ViewAgenda page.
                # The link goes through Office viewer: view.officeapps.live.com/op/view.aspx?src=XLSX_URL
                # We extract the src= parameter directly — no click/navigation needed.
                if not downloaded:
                    try:
                        log.info("  Trying to find Excel link on ViewAgenda page…")
                        doc_url = f"{BASE_URL}/Documents/ViewAgenda?meetingId={meeting['meeting_id']}&doctype=1"
                        await page.goto(doc_url, wait_until="networkidle", timeout=20000)

                        comment_link = await page.query_selector(
                            "a:has-text('Click Here to View Comments'), "
                            "a[href*='public-comments'], a[href$='.xlsx']"
                        )
                        if comment_link:
                            href = await comment_link.get_attribute("href") or ""
                            # Extract direct xlsx URL from Office viewer src param
                            qs = parse_qs(urlparse(href).query)
                            xlsx_url = qs.get("src", [None])[0] or (href if href.endswith(".xlsx") else None)
                            if xlsx_url:
                                log.info(f"  Found Excel URL from page: {xlsx_url}")
                                downloaded = try_download_excel(xlsx_url, excel_path)
                                if downloaded:
                                    conn.execute(
                                        "UPDATE meetings SET excel_url=? WHERE meeting_id=?",
                                        (xlsx_url, meeting["meeting_id"])
                                    )
                                    conn.commit()
                    except Exception as e:
                        log.warning(f"  Fallback Excel link extraction failed: {e}")
            else:
                log.info(f"  Excel already exists: {excel_path.name}")
                downloaded = True

            # 2c. Parse Excel → comments
            if excel_path.exists():
                comments = parse_excel_comments(excel_path, meeting["meeting_id"], meeting["date"])
                if comments:
                    save_comments(conn, comments)
                    stats["comments"] += len(comments)
                    log.info(f"  → Ingested {len(comments)} comments")

            await asyncio.sleep(0.8)  # polite delay

        await browser.close()

    # ── Step 3: Resolve comment → agenda item links ────────────────────────
    log.info("\nResolving comment → agenda item mappings…")
    resolve_comment_items(conn)

    # ── Step 4: Summary stats ──────────────────────────────────────────────
    conn.execute(
        """INSERT INTO scrape_log (run_at, meetings_found, items_found, comments_found, errors)
           VALUES (?, ?, ?, ?, ?)""",
        (datetime.utcnow().isoformat(), stats["meetings"], stats["items"],
         stats["comments"], json.dumps(stats["errors"]))
    )
    conn.commit()

    log.info(f"""
╔══════════════════════════════╗
║   Scrape complete            ║
╠══════════════════════════════╣
║  Meetings  : {stats['meetings']:>5}            ║
║  Items     : {stats['items']:>5}            ║
║  Comments  : {stats['comments']:>5}            ║
║  Errors    : {len(stats['errors']):>5}            ║
╚══════════════════════════════╝
    """)

    # Mapping quality report
    cur = conn.execute(
        "SELECT COUNT(*) FROM public_comments WHERE raw_item_ref != '' AND raw_item_ref IS NOT NULL"
    )
    total_ref = cur.fetchone()[0]
    cur = conn.execute(
        "SELECT COUNT(*) FROM public_comments WHERE agenda_item_id IS NOT NULL"
    )
    matched = cur.fetchone()[0]
    if total_ref:
        log.info(f"Comment→Item match rate: {matched}/{total_ref} ({100*matched//total_ref}%)")

    conn.close()


def run(output_dir, start_date, limit, headless):
    asyncio.run(run_async(output_dir, start_date, limit, headless))


def reparse_comments(output_dir: str):
    """Re-parse all cached Excel files and rebuild public_comments. No browser needed."""
    output = Path(output_dir)
    db_path = output / "council.db"
    excel_dir = output / "excel_files"

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    meetings = {
        row["meeting_id"]: row["date"]
        for row in conn.execute("SELECT meeting_id, date FROM meetings").fetchall()
    }

    excel_files = sorted(excel_dir.glob("comments_*.xlsx"))
    log.info(f"Found {len(excel_files)} cached Excel files to re-parse")

    conn.execute("DELETE FROM public_comments")
    conn.commit()
    log.info("Cleared public_comments table")

    total = 0
    for path in excel_files:
        try:
            meeting_id = int(path.stem.split("_")[1])
        except (IndexError, ValueError):
            continue
        meeting_date = meetings.get(meeting_id, "")
        comments = parse_excel_comments(path, meeting_id, meeting_date)
        if comments:
            save_comments(conn, comments)
            total += len(comments)
            log.info(f"  {path.name}: {len(comments)} comments")

    log.info(f"\nRe-parsed {total} comments total. Resolving agenda item links…")
    resolve_comment_items(conn)
    conn.close()
    log.info("Done.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SD City Council scraper")
    parser.add_argument("--output",           default="../data",  help="Output directory")
    parser.add_argument("--start",            default=None,       help="Start date YYYY-MM-DD")
    parser.add_argument("--limit",            type=int,           help="Max meetings (for testing)")
    parser.add_argument("--no-headless",      action="store_true",help="Show browser window")
    parser.add_argument("--reparse-comments", action="store_true",
                        help="Re-parse cached Excel files and rebuild public_comments (no scraping)")
    args = parser.parse_args()

    if args.reparse_comments:
        reparse_comments(args.output)
    else:
        run(args.output, args.start, args.limit, headless=not args.no_headless)
