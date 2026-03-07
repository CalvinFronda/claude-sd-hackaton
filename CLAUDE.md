# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

SD City Council — Community Voice Pipeline. Scrapes San Diego City Council agendas and public comment Excel files, maps comments to agenda items, classifies by theme/sentiment via Claude API, and surfaces everything in a Streamlit dashboard.

All source files live in `scraper/`. Data is written to `data/` (auto-created).

## Commands

All commands run from the repo root using the venv in `scraper/.venv/`.

```bash
# Activate venv
source scraper/.venv/bin/activate

# Scrape (test: 5 meetings, browser visible)
python scraper/scraper.py --output ./data --limit 5 --no-headless

# Scrape historical data
python scraper/scraper.py --output ./data --start 2024-01-01

# NLP classification (requires ANTHROPIC_API_KEY in scraper/.env)
python scraper/nlp_pipeline.py --db ./data/council.db

# Reset NLP and re-run
python scraper/nlp_pipeline.py --db ./data/council.db --reset

# Dashboard
streamlit run scraper/dashboard.py -- --db ./data/council.db
```

## Architecture

**Data flow:** Playwright scrapes meeting list → per-meeting agenda items extracted → Excel comment files downloaded → comments parsed → `resolve_comment_items()` maps comments to agenda items via normalized item number matching → `nlp_pipeline.py` classifies with Claude API → dashboard reads SQLite.

**Key files:**
- `scraper/scraper.py` — Playwright scraper, Excel parser, item mapper. Core logic: `resolve_comment_items()` normalizes item refs ("200 A" → "200a") and FK-links comments to `agenda_items`.
- `scraper/nlp_pipeline.py` — Batches unprocessed comments through Claude (`claude-sonnet-4-20250514`), writes `themes`, `sentiment`, `keywords`, `ai_summary` back to DB.
- `scraper/dashboard.py` — Streamlit app with 5 tabs: Themes, By Agenda Item, Trends, Comments, Mapping Quality.
- `scraper/schema.sql` — SQLite schema (source of truth). Tables: `meetings`, `agenda_items`, `public_comments`, `themes`, `comment_themes`, `scrape_log`.

**API key:** stored in `scraper/.env` as `ANTHROPIC_API_KEY`. Loaded via `python-dotenv` in `nlp_pipeline.py`.

**Item matching:** Two strategies in `scrape_agenda_items()` — structured CSS selectors first, then full-text regex fallback. Excel URL follows a predictable pattern; fallback clicks the "view comments" link on the agenda page.
