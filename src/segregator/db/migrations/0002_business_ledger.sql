-- 0002_business_ledger.sql — Бухгалтерский реестр KPiR, налоги и ZUS

CREATE TABLE IF NOT EXISTS kpir_entries (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id            INTEGER REFERENCES documents(id),
    lp                     INTEGER NOT NULL,
    entry_date             TEXT NOT NULL,          -- ISO-8601 YYYY-MM-DD
    doc_number             TEXT NOT NULL,
    counterparty_name      TEXT NOT NULL,
    counterparty_address   TEXT DEFAULT '',
    description            TEXT NOT NULL,
    col_7_przychody        REAL NOT NULL DEFAULT 0.00,
    col_8_pozostale_przych REAL NOT NULL DEFAULT 0.00,
    col_9_razem_przychody  REAL NOT NULL DEFAULT 0.00,
    col_10_zakup_towarow   REAL NOT NULL DEFAULT 0.00,
    col_11_koszty_uboczne  REAL NOT NULL DEFAULT 0.00,
    col_12_wynagrodzenia   REAL NOT NULL DEFAULT 0.00,
    col_13_pozostale_wyd   REAL NOT NULL DEFAULT 0.00,
    col_14_razem_wydatki   REAL NOT NULL DEFAULT 0.00,
    vat_amount             REAL NOT NULL DEFAULT 0.00,
    vehicle_usage          TEXT,                   -- 'mixed' | 'business_only' | 'private'
    kup_ratio              REAL DEFAULT 1.00,
    vat_ratio              REAL DEFAULT 1.00,
    raw_facts_json         TEXT,                   -- DocumentFacts в JSON, ЗАМАСКИРОВАННЫЙ:
                                                   -- счета, PESEL и номера карт проходят через
                                                   -- mask_sensitive_fields() перед записью
                                                   -- (DATA_BOUNDARY.md, инвариант 3). Не сырьё.
    created_at             TEXT NOT NULL           -- ISO-8601 UTC
);

CREATE TABLE IF NOT EXISTS zus_declarations (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    taxpayer_nip           TEXT NOT NULL,
    period_month           TEXT NOT NULL,          -- YYYY-MM
    stage                  TEXT NOT NULL,          -- ulga_na_start | preferencyjny | maly_zus_plus | duzy_zus
    zbieg_tytulow          INTEGER NOT NULL DEFAULT 0,
    spoleczne_base         REAL NOT NULL DEFAULT 0.00,
    zdrowotna_base         REAL NOT NULL DEFAULT 0.00,
    emerytalne             REAL NOT NULL DEFAULT 0.00,
    rentowe                REAL NOT NULL DEFAULT 0.00,
    chorobowe              REAL NOT NULL DEFAULT 0.00,
    wypadkowe              REAL NOT NULL DEFAULT 0.00,
    fundusz_pracy          REAL NOT NULL DEFAULT 0.00,
    skladka_zdrowotna      REAL NOT NULL DEFAULT 0.00,
    total_spoleczne        REAL NOT NULL DEFAULT 0.00,
    total_do_zaplaty       REAL NOT NULL DEFAULT 0.00,
    forms_json             TEXT,                   -- ['ZUS DRA', 'ZUS RCA']
    created_at             TEXT NOT NULL,
    UNIQUE (taxpayer_nip, period_month)
);

CREATE TABLE IF NOT EXISTS tax_advances (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    taxpayer_nip           TEXT NOT NULL,
    period_month           TEXT NOT NULL,          -- YYYY-MM
    regime                 TEXT NOT NULL,          -- skala | liniowy | ryczalt
    income_ytd             REAL NOT NULL DEFAULT 0.00,
    costs_ytd              REAL NOT NULL DEFAULT 0.00,
    tax_base_ytd           REAL NOT NULL DEFAULT 0.00,
    tax_due_ytd            REAL NOT NULL DEFAULT 0.00,
    advances_paid_prior    REAL NOT NULL DEFAULT 0.00,
    advance_to_pay         REAL NOT NULL DEFAULT 0.00,
    threshold_exceeded     INTEGER NOT NULL DEFAULT 0,
    created_at             TEXT NOT NULL,
    UNIQUE (taxpayer_nip, period_month)
);

CREATE TABLE IF NOT EXISTS sync_watermarks (
    nip                    TEXT PRIMARY KEY,
    ksef_last_sync         TEXT,
    bank_last_sync         TEXT,
    telegram_last_msg_id   INTEGER,
    updated_at             TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_kpir_date ON kpir_entries(entry_date);
CREATE INDEX IF NOT EXISTS idx_kpir_doc ON kpir_entries(document_id);
