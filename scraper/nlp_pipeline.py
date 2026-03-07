"""
NLP Theme Extraction Pipeline
==============================
Reads public comments from council.db, classifies each by civic theme
and sentiment using the Claude API, and writes results back.

Usage:
    export ANTHROPIC_API_KEY=sk-ant-...
    python nlp_pipeline.py --db ../data/council.db
    python nlp_pipeline.py --db ../data/council.db --reset
"""

import sqlite3
import json
import time
import logging
import argparse
import os
from pathlib import Path
from datetime import datetime
import anthropic
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Theme taxonomy
# ─────────────────────────────────────────────────────────────────────────────

THEMES = [
    {"name": "Housing & Development",          "color": "#4A90D9"},
    {"name": "Homelessness & Social Services", "color": "#E8A838"},
    {"name": "Public Safety",                  "color": "#D94F4F"},
    {"name": "Environment & Climate",          "color": "#52B788"},
    {"name": "Transportation & Infrastructure","color": "#9B5DE5"},
    {"name": "Budget & Finance",               "color": "#F15BB5"},
    {"name": "Community & Neighborhoods",      "color": "#00B4D8"},
    {"name": "Land Use & Planning",            "color": "#FB8500"},
    {"name": "Business & Economy",             "color": "#8338EC"},
    {"name": "Equity & Social Justice",        "color": "#06D6A0"},
    {"name": "Education & Youth",              "color": "#FFD166"},
    {"name": "Other / General",                "color": "#ADB5BD"},
]

THEME_NAMES = [t["name"] for t in THEMES]

SYSTEM_PROMPT = f"""You are a civic analyst classifying San Diego City Council public comments.

Theme list (use exact names only):
{json.dumps(THEME_NAMES)}

For each comment return ONLY a JSON object:
{{
  "id": <integer, echo back the comment id>,
  "themes": ["Theme Name"],          // 1-3 themes, most relevant first
  "sentiment": "support|oppose|neutral|mixed",  // toward the agenda item / city action
  "keywords": ["keyword1", "keyword2", "keyword3"],  // 3-5 specific phrases
  "ai_summary": "One sentence: who is concerned about what and why."
}}

Strict rules:
- Return ONLY valid JSON, no markdown, no preamble.
- Themes must match the list exactly.
- sentiment is the commenter's stance toward the item/proposal, not their mood.
- For non-agenda / general comments, use "neutral" unless they explicitly oppose or support something.
"""


# ─────────────────────────────────────────────────────────────────────────────
# API calls
# ─────────────────────────────────────────────────────────────────────────────

def classify_batch(client: anthropic.Anthropic, batch: list[dict]) -> dict[int, dict]:
    """Classify a batch of comments in a single API call. Returns {id: result}."""
    if not batch:
        return {}

    user_content = "\n\n---\n\n".join(
        f"COMMENT ID: {c['id']}\nAGENDA ITEM: {c['raw_item_ref'] or 'N/A'}\n"
        f"POSITION: {c['position'] or 'N/A'}\n\nTEXT:\n{c['comment_text'][:1200]}"
        for c in batch
    )

    try:
        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4096,
            system=SYSTEM_PROMPT + "\n\nYou will receive multiple comments. Return a JSON ARRAY of objects, one per comment.",
            messages=[{"role": "user", "content": user_content}],
        )
        raw = message.content[0].text.strip()
        raw = raw.replace("```json", "").replace("```", "").strip()

        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return {item["id"]: item for item in parsed if "id" in item}
        if isinstance(parsed, dict) and "id" in parsed:
            return {parsed["id"]: parsed}
        return {}

    except json.JSONDecodeError:
        log.warning("Batch JSON parse failed — falling back to individual calls")
        results = {}
        for c in batch:
            try:
                msg = client.messages.create(
                    model="claude-sonnet-4-20250514",
                    max_tokens=400,
                    system=SYSTEM_PROMPT,
                    messages=[{"role": "user", "content":
                        f"COMMENT ID: {c['id']}\nAGENDA ITEM: {c['raw_item_ref'] or 'N/A'}\n"
                        f"POSITION: {c['position'] or 'N/A'}\n\nTEXT:\n{c['comment_text'][:1200]}"}],
                )
                r = json.loads(msg.content[0].text.replace("```json","").replace("```","").strip())
                results[c["id"]] = r
                time.sleep(0.2)
            except Exception as e:
                log.error(f"Individual classify failed for {c['id']}: {e}")
        return results

    except Exception as e:
        log.error(f"Batch API error: {e}")
        return {}


# ─────────────────────────────────────────────────────────────────────────────
# Database
# ─────────────────────────────────────────────────────────────────────────────

def ensure_themes(conn: sqlite3.Connection) -> dict[str, int]:
    for t in THEMES:
        conn.execute(
            "INSERT OR IGNORE INTO themes (name, color) VALUES (?, ?)",
            (t["name"], t["color"])
        )
    conn.commit()
    cur = conn.execute("SELECT id, name FROM themes")
    return {row[1]: row[0] for row in cur.fetchall()}


def get_unprocessed(conn: sqlite3.Connection, limit: int = 200) -> list[dict]:
    cur = conn.execute(
        """SELECT id, raw_item_ref, position, comment_text
           FROM public_comments
           WHERE themes IS NULL
           ORDER BY meeting_date DESC, id
           LIMIT ?""",
        (limit,)
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def save_results(conn: sqlite3.Connection, comment_id: int,
                 result: dict, theme_map: dict[str, int]):
    themes    = result.get("themes", [])
    sentiment = result.get("sentiment", "neutral")
    keywords  = result.get("keywords", [])
    summary   = result.get("ai_summary", "")

    conn.execute(
        """UPDATE public_comments
           SET themes=?, sentiment=?, keywords=?, ai_summary=?
           WHERE id=?""",
        (json.dumps(themes), sentiment, json.dumps(keywords), summary, comment_id)
    )

    for theme_name in themes:
        tid = theme_map.get(theme_name)
        if tid:
            conn.execute(
                "INSERT OR REPLACE INTO comment_themes (comment_id, theme_id, confidence) VALUES (?,?,?)",
                (comment_id, tid, round(1.0 / len(themes), 3))
            )
    conn.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def run(db_path: str, batch_size: int = 20, reset: bool = False):
    conn = sqlite3.connect(db_path)

    if reset:
        log.info("Resetting NLP fields…")
        conn.execute("UPDATE public_comments SET themes=NULL, sentiment=NULL, keywords=NULL, ai_summary=NULL")
        conn.execute("DELETE FROM comment_themes")
        conn.commit()

    theme_map = ensure_themes(conn)

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise SystemExit("Set ANTHROPIC_API_KEY environment variable")

    client = anthropic.Anthropic(api_key=api_key)
    total = 0

    while True:
        batch = get_unprocessed(conn, limit=batch_size * 4)
        if not batch:
            break

        for i in range(0, len(batch), batch_size):
            chunk = batch[i:i+batch_size]
            log.info(f"Classifying comments {total+1}–{total+len(chunk)}…")

            results = classify_batch(client, chunk)
            for c in chunk:
                r = results.get(c["id"])
                if r:
                    save_results(conn, c["id"], r, theme_map)
                    total += 1

            time.sleep(1.2)

    log.info(f"\n✅ Classified {total} comments")

    # Summary
    cur = conn.execute("""
        SELECT t.name, COUNT(ct.comment_id) as n
        FROM themes t LEFT JOIN comment_themes ct ON t.id=ct.theme_id
        GROUP BY t.id ORDER BY n DESC
    """)
    log.info("\n📊 Theme breakdown:")
    for name, n in cur.fetchall():
        bar = "█" * min(n, 40)
        log.info(f"  {name:<38} {n:>4}  {bar}")

    conn.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--db",         required=True)
    p.add_argument("--batch-size", type=int, default=20)
    p.add_argument("--reset",      action="store_true")
    args = p.parse_args()
    run(args.db, args.batch_size, args.reset)
