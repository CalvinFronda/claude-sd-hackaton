#!/usr/bin/env bash
set -e

echo "=== SD City Council Community Voice Pipeline — Setup ==="

# Check Python
if ! command -v python3 &>/dev/null; then
    echo "ERROR: python3 is required but not found. Install it from https://python.org"
    exit 1
fi

PYTHON_VERSION=$(python3 -c 'import sys; print(sys.version_info.minor)')
if [ "$PYTHON_VERSION" -lt 11 ]; then
    echo "WARNING: Python 3.11+ recommended (you have 3.$PYTHON_VERSION)"
fi

# Create virtualenv
echo ""
echo "→ Creating virtual environment at scraper/.venv ..."
python3 -m venv scraper/.venv

# Activate
source scraper/.venv/bin/activate

# Install dependencies
echo "→ Installing Python dependencies ..."
pip install --quiet --upgrade pip
pip install --quiet -r scraper/requirements.txt

# Install Playwright browsers
echo "→ Installing Playwright browsers (Chromium) ..."
playwright install chromium

# Create data directory
mkdir -p data

# Check for .env
if [ ! -f scraper/.env ]; then
    echo ""
    echo "→ Creating scraper/.env from template ..."
    echo "ANTHROPIC_API_KEY=your_api_key_here" > scraper/.env
    echo ""
    echo "  *** ACTION REQUIRED ***"
    echo "  Edit scraper/.env and add your Anthropic API key."
    echo "  Get one at: https://console.anthropic.com"
else
    echo "→ scraper/.env already exists — skipping."
fi

echo ""
echo "=== Setup complete! ==="
echo ""
echo "Next steps:"
echo "  1. Edit scraper/.env and set your ANTHROPIC_API_KEY (if not done)"
echo "  2. Activate the venv:  source scraper/.venv/bin/activate"
echo "  3. Run the scraper:    python scraper/scraper.py --output ./data --limit 5"
echo "  4. Run NLP pipeline:   python scraper/nlp_pipeline.py --db ./data/council.db"
echo "  5. Launch dashboard:   streamlit run scraper/dashboard.py -- --db ./data/council.db"
echo ""
