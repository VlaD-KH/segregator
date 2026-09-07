-- Хранилище prospektor. SQLite в режиме WAL: один писатель, много читателей —
-- ровно то, что нужно, когда CLI пишет прогон, а дашборд одновременно читает.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS runs (
    id           TEXT PRIMARY KEY,
    stage        TEXT NOT NULL,
    area         TEXT,
    profile      TEXT,
    legal_mode   TEXT NOT NULL DEFAULT 'clean',
    sources_used TEXT NOT NULL DEFAULT '[]',
    cost_spent   REAL NOT NULL DEFAULT 0.0,
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    notes        TEXT
);

CREATE TABLE IF NOT EXISTS businesses (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    country       TEXT NOT NULL DEFAULT 'PL',
    city          TEXT,
    postal_code   TEXT,
    street        TEXT,
    lat           REAL,
    lon           REAL,
    phone         TEXT,
    email         TEXT,
    website       TEXT,
    categories    TEXT NOT NULL DEFAULT '[]',
    cuisines      TEXT NOT NULL DEFAULT '[]',
    rating        REAL,
    reviews_count INTEGER,
    first_seen    TEXT NOT NULL,
    last_seen     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_businesses_city ON businesses(country, city);

-- Внешние идентификаторы. Отдельной таблицей, потому что один бизнес живёт
-- одновременно в OSM, Overture, Google и CEIDG, и сшивание идёт именно по ним.
CREATE TABLE IF NOT EXISTS refs (
    business_id TEXT NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL,
    value       TEXT NOT NULL,
    PRIMARY KEY (business_id, kind)
);
CREATE INDEX IF NOT EXISTS idx_refs_value ON refs(kind, value);

-- Сырые утверждения источников. Конфликты не разрешаются на записи — они
-- остаются в таблице, и видно, кто что сказал и когда.
CREATE TABLE IF NOT EXISTS facts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    business_id   TEXT NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    field         TEXT NOT NULL,
    value         TEXT NOT NULL,
    source        TEXT NOT NULL,
    source_url    TEXT,
    confidence    REAL NOT NULL DEFAULT 0.5,
    personal_data INTEGER NOT NULL DEFAULT 0,
    fetched_at    TEXT NOT NULL,
    UNIQUE (business_id, field, value, source)
);
CREATE INDEX IF NOT EXISTS idx_facts_business ON facts(business_id, field);

CREATE TABLE IF NOT EXISTS signals (
    business_id  TEXT NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    key          TEXT NOT NULL,
    kind         TEXT NOT NULL,
    value_bool   INTEGER,
    value_num    REAL,
    value_json   TEXT,
    evidence_url TEXT,
    snippet_sha  TEXT,
    checked_at   TEXT NOT NULL,
    PRIMARY KEY (business_id, key)
);
CREATE INDEX IF NOT EXISTS idx_signals_key ON signals(key);

CREATE TABLE IF NOT EXISTS scores (
    business_id TEXT NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    profile     TEXT NOT NULL,
    gap         REAL NOT NULL,
    fit         REAL NOT NULL,
    priority    REAL NOT NULL,
    reachable   INTEGER NOT NULL DEFAULT 0,
    breakdown   TEXT NOT NULL DEFAULT '{}',
    scored_at   TEXT NOT NULL,
    PRIMARY KEY (business_id, profile)
);
CREATE INDEX IF NOT EXISTS idx_scores_priority ON scores(profile, priority DESC);

-- Кэш загрузок. Держит аудит идемпотентным: повторный прогон не ходит в сеть,
-- пока не истёк TTL, а прерванный прогон продолжается, а не начинается заново.
CREATE TABLE IF NOT EXISTS fetch_cache (
    url          TEXT NOT NULL,
    mode         TEXT NOT NULL DEFAULT 'raw',
    status       INTEGER,
    headers      TEXT NOT NULL DEFAULT '{}',
    body         TEXT,
    error        TEXT,
    fetched_at   TEXT NOT NULL,
    PRIMARY KEY (url, mode)
);

-- Срок хранения. RODO требует не держать персональные данные дольше нужного,
-- поэтому purge — не служебная команда, а часть контракта модуля.
CREATE TABLE IF NOT EXISTS retention (
    business_id TEXT PRIMARY KEY REFERENCES businesses(id) ON DELETE CASCADE,
    collected_at TEXT NOT NULL,
    purge_after  TEXT NOT NULL
);
