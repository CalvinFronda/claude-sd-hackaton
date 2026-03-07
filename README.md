# SD City Council — Community Voice Pipeline

Scrapes San Diego City Council agendas and public comment Excel files, classifies comments by theme/sentiment via Claude AI, and surfaces everything in a Streamlit dashboard.

## Quickstart

**Requirements:** Python 3.11+, Git

```bash
git clone <repo-url>
cd claude-sd-hackaton

# Run setup (creates venv, installs deps, installs Playwright browser)
./setup.sh

# Add your Anthropic API key
echo "ANTHROPIC_API_KEY=sk-ant-..." > scraper/.env
```

Then run the pipeline:

```bash
source scraper/.venv/bin/activate

# 1. Scrape meetings (--limit 5 for a quick test)
python scraper/scraper.py --output ./data --limit 5

# 2. Classify comments with Claude
python scraper/nlp_pipeline.py --db ./data/council.db

# 3. Launch the dashboard
streamlit run scraper/dashboard.py -- --db ./data/council.db
```

Open [http://localhost:8501](http://localhost:8501) to view the dashboard.

## Dashboard tabs

| Tab | What it shows |
|-----|---------------|
| Themes | Bar chart of civic themes, sentiment breakdown |
| By Agenda Item | Which items got the most comments, support/oppose |
| Trends | Monthly comment volume, top themes over time |
| Comments | Paginated browser with search and filter |
| Mapping Quality | Matched vs unmatched comment-to-item refs |

## Notes

- `scraper/.env` holds your `ANTHROPIC_API_KEY` — never commit this file (it's gitignored)
- Scraped data lives in `data/council.db` (SQLite) — also gitignored, each teammate runs the scraper locally
- The NLP pipeline requires credits on your Anthropic account
