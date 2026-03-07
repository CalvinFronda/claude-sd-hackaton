-- ============================================================
-- San Diego City Council — SQLite Schema
-- ============================================================

-- One row per council meeting day (Mon/Tue/Special etc.)
CREATE TABLE IF NOT EXISTS meetings (
    meeting_id      INTEGER PRIMARY KEY,   -- OnBase numeric ID (e.g. 6880)
    title           TEXT NOT NULL,         -- "Tuesday Agenda and Results Summary"
    date            TEXT NOT NULL,         -- ISO-8601: "2026-03-03"
    day_of_week     TEXT,                  -- "Monday" | "Tuesday" | etc.
    meeting_type    TEXT,                  -- "Regular" | "Special" | "Closed Session"
    agenda_url      TEXT,                  -- full OnBase ViewMeeting URL
    excel_url       TEXT,                  -- direct sandiego.gov xlsx URL (may be null)
    scraped_at      TEXT
);

-- One row per line item on a meeting agenda
CREATE TABLE IF NOT EXISTS agenda_items (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_id      INTEGER NOT NULL,
    item_number     TEXT NOT NULL,         -- "100", "200A", "S-1", "B-1" etc.
    item_number_norm TEXT NOT NULL,        -- normalised for matching: "100", "200a"
    title           TEXT,                  -- short heading from agenda
    description     TEXT,                  -- full body text if available
    category        TEXT,                  -- section heading ("CONSENT", "ITEMS", etc.)
    district        TEXT,                  -- council district if mentioned
    FOREIGN KEY (meeting_id) REFERENCES meetings(meeting_id)
);

-- One row per public comment row in the Excel file
CREATE TABLE IF NOT EXISTS public_comments (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_id      INTEGER NOT NULL,
    meeting_date    TEXT,

    -- Raw fields from Excel
    commenter_name  TEXT,
    email           TEXT,
    phone           TEXT,
    address         TEXT,
    district        TEXT,
    raw_item_ref    TEXT,   -- exactly as it appeared in the Excel "Agenda Item" column
    position        TEXT,   -- "In Support" | "In Opposition" | "General Comment" etc.
    comment_text    TEXT NOT NULL,
    submitted_at    TEXT,
    source_file     TEXT,

    -- Resolved FK to agenda_items (NULL if unmatched or non-agenda comment)
    agenda_item_id  INTEGER,

    -- NLP-enriched fields (populated by nlp_pipeline.py)
    themes          TEXT,       -- JSON array  e.g. ["Housing & Development"]
    sentiment       TEXT,       -- "support" | "oppose" | "neutral" | "mixed"
    keywords        TEXT,       -- JSON array  e.g. ["rent control", "ADU"]
    ai_summary      TEXT,       -- 1-sentence Claude summary

    FOREIGN KEY (meeting_id)    REFERENCES meetings(meeting_id),
    FOREIGN KEY (agenda_item_id) REFERENCES agenda_items(id)
);

-- Lookup: normalised theme taxonomy
CREATE TABLE IF NOT EXISTS themes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT UNIQUE NOT NULL,
    description TEXT,
    color       TEXT        -- hex colour for UI
);

-- Junction: comment ↔ theme (many-to-many)
CREATE TABLE IF NOT EXISTS comment_themes (
    comment_id  INTEGER NOT NULL,
    theme_id    INTEGER NOT NULL,
    confidence  REAL DEFAULT 1.0,
    PRIMARY KEY (comment_id, theme_id),
    FOREIGN KEY (comment_id) REFERENCES public_comments(id),
    FOREIGN KEY (theme_id)   REFERENCES themes(id)
);

-- Scrape-run audit log
CREATE TABLE IF NOT EXISTS scrape_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at          TEXT,
    meetings_found  INTEGER,
    items_found     INTEGER,
    comments_found  INTEGER,
    errors          TEXT    -- JSON array of error messages
);

-- Indexes for common query patterns
CREATE INDEX IF NOT EXISTS idx_comments_meeting   ON public_comments(meeting_id);
CREATE INDEX IF NOT EXISTS idx_comments_item      ON public_comments(agenda_item_id);
CREATE INDEX IF NOT EXISTS idx_items_meeting      ON agenda_items(meeting_id);
CREATE INDEX IF NOT EXISTS idx_items_number       ON agenda_items(item_number_norm, meeting_id);
