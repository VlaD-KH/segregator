-- 0003_audit_quarantine.sql — ślad rewizyjny, карантин эскалированных документов,
-- запрет дублей проводок и append-only для помесячных расчётов.
--
-- Схема 0001+0002 нарушала инвариант 5 DATA_BOUNDARY.md в трёх местах:
-- AuditEntry (orchestrator/state.py) дописывался в семи узлах и не сохранялся никуда;
-- дубль проводки одного документа ничем не запрещался;
-- UNIQUE (taxpayer_nip, period_month) вынуждал INSERT OR REPLACE, затиравший
-- прошлый расчёт вместе с его created_at.
--
-- Транзакция явная: перестройка таблиц ниже не должна применяться наполовину.

BEGIN;

-- 1. Аудит ---------------------------------------------------------------------
-- Append-only по построению: строки только добавляются, UPDATE по таблице нет.

CREATE TABLE IF NOT EXISTS audit_trail (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER REFERENCES documents(id),
    node_name   TEXT NOT NULL,          -- узел графа: agent01 | agent02 | ...
    action      TEXT NOT NULL,
    details     TEXT NOT NULL DEFAULT '',
    confidence  REAL NOT NULL DEFAULT 1.00,
    ts          TEXT NOT NULL           -- ISO-8601 UTC, AuditEntry.timestamp
);

CREATE INDEX IF NOT EXISTS idx_audit_document ON audit_trail(document_id, ts);

-- 2. Карантин ------------------------------------------------------------------
-- Решение владельца: эскалированный документ попадает в архив, но его проводка
-- идёт сюда, а не в kpir_entries с флагом. Флаг обязывал бы каждого нового
-- читателя реестра помнить про фильтр; отдельная таблица не обязывает никого.
-- Форма повторяет kpir_entries, чтобы выпуск из карантина был INSERT ... SELECT.

CREATE TABLE IF NOT EXISTS kpir_quarantine (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id            INTEGER REFERENCES documents(id),
    lp                     INTEGER,                -- NULL, пока документ в карантине:
                                                   -- номер KPiR выдаётся при проводке,
                                                   -- иначе в реестре осталась бы дыра
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
    raw_facts_json         TEXT,                   -- снимок DocumentFacts, пропущенный
                                                   -- через mask_sensitive_fields()
                                                   -- (DATA_BOUNDARY.md, инвариант 3)
    escalation_reason      TEXT NOT NULL,          -- почему документ не проведён
    created_at             TEXT NOT NULL           -- ISO-8601 UTC
);

CREATE INDEX IF NOT EXISTS idx_quarantine_document ON kpir_quarantine(document_id);

-- 3. Запрет дублей проводок ----------------------------------------------------
-- Одну и ту же проводку документа нельзя внести дважды. Сторно остаётся
-- возможным: это вторая строка того же документа со своим lp.

CREATE UNIQUE INDEX IF NOT EXISTS idx_kpir_doc_lp ON kpir_entries(document_id, lp);

-- 4. История расчётов вместо перезаписи ----------------------------------------
-- UNIQUE (taxpayer_nip, period_month) объявлен на уровне таблицы, ALTER TABLE его
-- не снимает — обе таблицы перестраиваются. Действующий расчёт по-прежнему один:
-- его держит частичный индекс по superseded_at IS NULL, а не запрет на историю.

CREATE TABLE zus_declarations_new (
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
    superseded_at          TEXT                    -- NULL = действующий расчёт
);

INSERT INTO zus_declarations_new (
    id, taxpayer_nip, period_month, stage, zbieg_tytulow, spoleczne_base, zdrowotna_base,
    emerytalne, rentowe, chorobowe, wypadkowe, fundusz_pracy, skladka_zdrowotna,
    total_spoleczne, total_do_zaplaty, forms_json, created_at
)
SELECT
    id, taxpayer_nip, period_month, stage, zbieg_tytulow, spoleczne_base, zdrowotna_base,
    emerytalne, rentowe, chorobowe, wypadkowe, fundusz_pracy, skladka_zdrowotna,
    total_spoleczne, total_do_zaplaty, forms_json, created_at
FROM zus_declarations;

DROP TABLE zus_declarations;
ALTER TABLE zus_declarations_new RENAME TO zus_declarations;

CREATE UNIQUE INDEX idx_zus_current
    ON zus_declarations(taxpayer_nip, period_month) WHERE superseded_at IS NULL;

CREATE TABLE tax_advances_new (
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
    superseded_at          TEXT                    -- NULL = действующий расчёт
);

INSERT INTO tax_advances_new (
    id, taxpayer_nip, period_month, regime, income_ytd, costs_ytd, tax_base_ytd,
    tax_due_ytd, advances_paid_prior, advance_to_pay, threshold_exceeded, created_at
)
SELECT
    id, taxpayer_nip, period_month, regime, income_ytd, costs_ytd, tax_base_ytd,
    tax_due_ytd, advances_paid_prior, advance_to_pay, threshold_exceeded, created_at
FROM tax_advances;

DROP TABLE tax_advances;
ALTER TABLE tax_advances_new RENAME TO tax_advances;

CREATE UNIQUE INDEX idx_tax_advances_current
    ON tax_advances(taxpayer_nip, period_month) WHERE superseded_at IS NULL;

COMMIT;
