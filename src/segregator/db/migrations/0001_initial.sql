-- 0001_initial.sql — базовая схема (ТЗ §08)

CREATE TABLE IF NOT EXISTS messages (
    id           INTEGER PRIMARY KEY,
    chat_id      INTEGER NOT NULL,
    message_id   INTEGER NOT NULL,
    sent_at      TEXT NOT NULL,          -- ISO-8601 UTC
    author       TEXT,
    body         TEXT,
    source       TEXT NOT NULL,          -- 'export' | 'live'
    raw          TEXT,                   -- исходный JSON
    UNIQUE (chat_id, message_id)
);

CREATE TABLE IF NOT EXISTS blobs (
    sha256       TEXT PRIMARY KEY,
    bytes        INTEGER NOT NULL,
    mime         TEXT,
    stored_path  TEXT NOT NULL,          -- blobs/ab/cd/...
    ocr_state    TEXT NOT NULL DEFAULT 'pending',  -- pending|text|ocr|failed
    text_content TEXT
);

CREATE TABLE IF NOT EXISTS attachments (
    id           INTEGER PRIMARY KEY,
    message_id   INTEGER NOT NULL REFERENCES messages(id),
    idx          INTEGER NOT NULL,
    sha256       TEXT NOT NULL REFERENCES blobs(sha256),
    orig_name    TEXT,
    UNIQUE (message_id, idx)
);

CREATE TABLE IF NOT EXISTS documents (
    id            INTEGER PRIMARY KEY,
    attachment_id INTEGER UNIQUE REFERENCES attachments(id),
    doc_type      TEXT,                  -- faktura|paragon|deklaracja|umowa|wyciag|inne
    category      TEXT,
    subcategory   TEXT,
    confidence    REAL NOT NULL DEFAULT 0,
    decided_by    TEXT,                  -- precedent|rule:<id>|llm-local|llm-remote|human
    doc_date      TEXT,
    sale_date     TEXT,
    due_date      TEXT,
    paid_date     TEXT,
    date_source   TEXT,                  -- extracted|message|manual
    counterparty  TEXT,
    nip           TEXT,
    doc_number    TEXT,
    net           REAL,
    vat           REAL,
    gross         REAL,
    currency      TEXT,
    tree_path     TEXT,
    link_path     TEXT,
    reviewed_at   TEXT
);

CREATE TABLE IF NOT EXISTS precedents (
    id          INTEGER PRIMARY KEY,
    key_kind    TEXT NOT NULL,           -- sha256|counterparty+doc_type
    key_value   TEXT NOT NULL,
    category    TEXT NOT NULL,
    subcategory TEXT,
    hits        INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL,
    UNIQUE (key_kind, key_value)
);

CREATE TABLE IF NOT EXISTS llm_cache (
    prompt_hash TEXT PRIMARY KEY,
    model       TEXT,
    response    TEXT,
    created_at  TEXT
);

CREATE TABLE IF NOT EXISTS runtime_state (
    key   TEXT PRIMARY KEY,
    value TEXT              -- last_update_id, mode, last_backfill...
);

CREATE VIRTUAL TABLE IF NOT EXISTS docs_fts USING fts5(
    text_content, counterparty, doc_number,
    content='blobs', tokenize='unicode61 remove_diacritics 2'
);

CREATE INDEX IF NOT EXISTS idx_documents_category ON documents(category, doc_date);
CREATE INDEX IF NOT EXISTS idx_attachments_message ON attachments(message_id);
