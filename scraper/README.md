# 🏛️ SD City Council — Community Voice Pipeline

Scrapes San Diego City Council agendas + public comment Excel files,
maps each comment to its specific agenda item, classifies by civic theme,
and surfaces everything in a Streamlit dashboard.

---

## How the data flows

```
sandiego.hylandcloud.com/211agendaonlinecouncil
  │
  ├── Meeting list (all agenda IDs)
  │     id=6880  →  "Tuesday Agenda and Results Summary"  2026-03-03
  │     id=6881  →  "Monday Agenda and Results Summary"   2026-03-02
  │     …
  │
  └── Per meeting agenda page  (Playwright renders JavaScript)
        ├── Agenda items:  100 · 200 · 200A · S-1 · B-100 …
        │
        └── "Click here to view comments" link
              │
              └──▶  sandiego.gov/.../MM-DD-YYYY-public-comments-submitted-via-webform.xlsx
                      │
                      ├── Row: Name · Email · Agenda Item: "200A" · Comment · Position
                      ├── Row: Name · Email · Agenda Item: "100"  · Comment · Position
                      └── Row: Name · Email · Agenda Item: ""     · Comment · Position
```

The **Excel URL follows a predictable pattern** — no login needed:
```
https://www.sandiego.gov/sites/default/files/{YYYY-MM}/{MM-DD-YYYY}-public-comments-submitted-via-webform.xlsx
```

---

## Comment → Agenda Item Mapping

This is the core mapping logic in `scraper.py → resolve_comment_items()`:

```
Excel "Agenda Item" column   →   normalise   →   lookup in agenda_items table
────────────────────────────────────────────────────────────────────────────
"200 A"                      →   "200a"       →   item_number_norm = "200a"  ✓
"Item B-100"                 →   "b-100"      →   item_number_norm = "b-100" ✓
"100, 200A"                  →   ["100","200a"]→   first match wins           ✓
"S-1"                        →   "s-1"        →   item_number_norm = "s-1"   ✓
"Non-Agenda"                 →   (skipped)    →   agenda_item_id = NULL      —
""  (blank)                  →   (skipped)    →   agenda_item_id = NULL      —
```

The `public_comments.agenda_item_id` FK ties every matched comment directly
to its `agenda_items` row, enabling queries like:

```sql
-- All comments for a specific item, with sentiment
SELECT pc.commenter_name, pc.comment_text, pc.sentiment, pc.themes
FROM public_comments pc
JOIN agenda_items ai ON pc.agenda_item_id = ai.id
WHERE ai.meeting_id = 6880 AND ai.item_number = '200'
ORDER BY pc.sentiment;

-- Which items got the most opposition?
SELECT ai.item_number, ai.title,
       COUNT(pc.id) as total,
       SUM(CASE WHEN pc.sentiment='oppose' THEN 1 ELSE 0 END) as opposition
FROM agenda_items ai
JOIN public_comments pc ON pc.agenda_item_id = ai.id
GROUP BY ai.id ORDER BY opposition DESC;
```

---

## Setup

```bash
pip install playwright pandas openpyxl requests anthropic streamlit plotly
playwright install chromium
export ANTHROPIC_API_KEY=sk-ant-...
```

## Run

```bash
# 1. Scrape (test first with --limit 5 --no-headless)
python scraper/scraper.py --output ./data --limit 5 --no-headless
python scraper/scraper.py --output ./data --start 2024-01-01

# 2. NLP classification
python scraper/nlp_pipeline.py --db ./data/council.db

# 3. Dashboard
streamlit run scraper/dashboard.py -- --db ./data/council.db
```

---

## Project structure

```
claude-sd-hackaton/
├── scraper/
│   ├── schema.sql          ← database schema (source of truth)
│   ├── scraper.py          ← Playwright scraper + Excel parser + item mapper
│   ├── nlp_pipeline.py     ← Claude API theme/sentiment classifier
│   ├── dashboard.py        ← Streamlit app
│   ├── requirements.txt
│   └── .env                ← ANTHROPIC_API_KEY
├── data/
│   ├── council.db          ← SQLite (auto-created)
│   └── excel_files/        ← Downloaded .xlsx files
└── README.md
```

---

## Database tables

| Table | Key columns |
|---|---|
| `meetings` | meeting_id, date, title, excel_url |
| `agenda_items` | id, meeting_id, **item_number**, item_number_norm, title, category |
| `public_comments` | id, meeting_id, raw_item_ref, comment_text, **agenda_item_id** → FK |
| `themes` | id, name, color |
| `comment_themes` | comment_id, theme_id, confidence |

---

## Troubleshooting

**Low item match rate**
Run `--no-headless` and inspect how item numbers appear on the rendered agenda page.
The scraper tries two strategies: structured element selectors, then full-text regex.
If both miss, add a custom CSS selector matching your agenda HTML to `scrape_agenda_items()`.

**Excel 404**
The predictable URL pattern may not apply to all dates (special meetings, holidays).
The scraper falls back to clicking the "view comments" link on the agenda page itself.

**Rate limiting**
The scraper waits ~0.8s between meetings. Increase `asyncio.sleep` if you hit 429s.
