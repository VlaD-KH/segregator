import sqlite3

import pytest

from segregator.db import migrate


def test_sqlite_build_has_fts5():
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
    finally:
        conn.close()


EXPECTED_TABLES = {
    "messages",
    "blobs",
    "attachments",
    "documents",
    "precedents",
    "llm_cache",
    "runtime_state",
    "docs_fts",
    "schema_migrations",
    "kpir_entries",
    "zus_declarations",
    "tax_advances",
    "sync_watermarks",
    "audit_trail",
    "kpir_quarantine",
}


def test_migrate_creates_all_tables(tmp_path):
    db_path = tmp_path / "segregator.db"
    applied = migrate.migrate(db_path)
    assert applied == [1, 2, 3]

    conn = migrate.get_connection(db_path)
    try:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view')").fetchall()
        table_names = {row[0] for row in rows}
    finally:
        conn.close()

    assert EXPECTED_TABLES <= table_names


def test_migrate_is_idempotent(tmp_path):
    db_path = tmp_path / "segregator.db"
    migrate.migrate(db_path)
    second_run = migrate.migrate(db_path)
    assert second_run == []

    conn = migrate.get_connection(db_path)
    try:
        count = conn.execute("SELECT COUNT(*) FROM schema_migrations WHERE version = 1").fetchone()[0]
    finally:
        conn.close()
    assert count == 1


# --- 0003: аудит, карантин, запрет дублей проводок, append-only расчётов ----------
#
# Каждый тест ниже наблюдался красным на схеме 0001+0002: таблиц аудита и карантина
# там нет, дубль проводки ничем не запрещён, а UNIQUE (taxpayer_nip, period_month)
# в 0002 делает историю расчётов невозможной в принципе.

TS = "2026-09-04T12:00:00+00:00"


@pytest.fixture
def db(tmp_path):
    """Мигрированная пустая база с одним документом id=1 и id=2."""
    db_path = tmp_path / "segregator.db"
    migrate.migrate(db_path)
    conn = migrate.get_connection(db_path)
    conn.execute("INSERT INTO documents (id) VALUES (1)")
    conn.execute("INSERT INTO documents (id) VALUES (2)")
    conn.commit()
    try:
        yield conn
    finally:
        conn.close()


def _insert_kpir(conn, document_id, lp, doc_number="FV/1/2026"):
    conn.execute(
        """
        INSERT INTO kpir_entries (
            document_id, lp, entry_date, doc_number, counterparty_name,
            description, created_at
        ) VALUES (?, ?, '2026-09-01', ?, 'Kontrahent', 'Usługa', ?)
        """,
        (document_id, lp, doc_number, TS),
    )
    conn.commit()


def _insert_zus(conn, period_month="2026-08", superseded_at=None):
    conn.execute(
        """
        INSERT INTO zus_declarations (
            taxpayer_nip, period_month, stage, created_at, superseded_at
        ) VALUES ('1234563218', ?, 'duzy_zus', ?, ?)
        """,
        (period_month, TS, superseded_at),
    )
    conn.commit()


def _insert_tax_advance(conn, period_month="2026-08", superseded_at=None):
    conn.execute(
        """
        INSERT INTO tax_advances (
            taxpayer_nip, period_month, regime, created_at, superseded_at
        ) VALUES ('1234563218', ?, 'liniowy', ?, ?)
        """,
        (period_month, TS, superseded_at),
    )
    conn.commit()


def test_kpir_rejects_duplicate_lp_for_same_document(db):
    """Дубль проводки одного документа под тем же номером — ошибка, а не вторая строка."""
    _insert_kpir(db, document_id=1, lp=1)
    with pytest.raises(sqlite3.IntegrityError):
        _insert_kpir(db, document_id=1, lp=1)


def test_kpir_allows_same_lp_for_different_documents(db):
    """Ограничение не должно быть шире нужного: lp уникален в пределах документа."""
    _insert_kpir(db, document_id=1, lp=1)
    _insert_kpir(db, document_id=2, lp=1)
    assert db.execute("SELECT COUNT(*) FROM kpir_entries").fetchone()[0] == 2


def test_kpir_allows_storno_row_for_same_document(db):
    """Сторно — вторая строка того же документа с собственным lp, не дубль."""
    _insert_kpir(db, document_id=1, lp=1)
    _insert_kpir(db, document_id=1, lp=2)
    assert db.execute("SELECT COUNT(*) FROM kpir_entries WHERE document_id = 1").fetchone()[0] == 2


def test_quarantine_keeps_document_out_of_kpir(db):
    """Эскалированный документ ложится в карантин и не занимает номер в реестре."""
    db.execute(
        """
        INSERT INTO kpir_quarantine (
            document_id, entry_date, doc_number, counterparty_name,
            description, escalation_reason, created_at
        ) VALUES (1, '2026-09-01', 'FV/1/2026', 'Kontrahent', 'Usługa',
                  'confidence 0.41 < 0.85', ?)
        """,
        (TS,),
    )
    db.commit()

    lp, reason = db.execute("SELECT lp, escalation_reason FROM kpir_quarantine").fetchone()
    assert lp is None, "карантинная строка не должна получать номер KPiR — он оставил бы дыру в реестре"
    assert reason == "confidence 0.41 < 0.85"
    assert db.execute("SELECT COUNT(*) FROM kpir_entries").fetchone()[0] == 0


def test_audit_trail_survives_second_processing_of_same_document(db):
    """Аудит append-only: повторная обработка добавляет запись, а не затирает прошлую."""
    for action in ("classified", "reclassified"):
        db.execute(
            """
            INSERT INTO audit_trail (document_id, node_name, action, details, confidence, ts)
            VALUES (1, 'agent02', ?, 'szczegóły', 0.9, ?)
            """,
            (action, TS),
        )
    db.commit()

    actions = [row[0] for row in db.execute(
        "SELECT action FROM audit_trail WHERE document_id = 1 ORDER BY id"
    )]
    assert actions == ["classified", "reclassified"]


@pytest.mark.parametrize("insert", [_insert_zus, _insert_tax_advance])
def test_recalculation_supersedes_instead_of_overwriting(db, insert):
    """Пересчёт месяца — новая строка при закрытой прошлой, а не INSERT OR REPLACE."""
    insert(db, superseded_at=TS)
    insert(db, superseded_at=None)

    table = "zus_declarations" if insert is _insert_zus else "tax_advances"
    assert db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 2
    assert db.execute(
        f"SELECT COUNT(*) FROM {table} WHERE superseded_at IS NULL"
    ).fetchone()[0] == 1


@pytest.mark.parametrize("insert", [_insert_zus, _insert_tax_advance])
def test_two_current_calculations_for_one_period_are_rejected(db, insert):
    """Действующий расчёт за период ровно один — иначе читателю не из чего выбрать."""
    insert(db, superseded_at=None)
    with pytest.raises(sqlite3.IntegrityError):
        insert(db, superseded_at=None)


def test_0003_preserves_rows_of_rebuilt_tables(tmp_path):
    """Перестройка zus_declarations и tax_advances не теряет уже посчитанное.

    Единственный путь, где 0003 видит данные: база, мигрированная до 0002, с
    расчётами внутри. Тесты выше идут по пустой базе и эту ветку не проходят.
    """
    db_path = tmp_path / "segregator.db"
    conn = migrate.get_connection(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE schema_migrations (
                version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL
            );
            INSERT INTO schema_migrations VALUES (1, 'initial', '2026-09-01T00:00:00+00:00'),
                                                 (2, 'business_ledger', '2026-09-01T00:00:00+00:00');
            """
        )
        for version, name in ((1, "0001_initial"), (2, "0002_business_ledger")):
            sql = (migrate.MIGRATIONS_DIR / f"{name}.sql").read_text(encoding="utf-8")
            conn.executescript(sql)
        conn.execute(
            """
            INSERT INTO zus_declarations (taxpayer_nip, period_month, stage, total_do_zaplaty, created_at)
            VALUES ('1234563218', '2026-07', 'duzy_zus', 1646.47, ?)
            """,
            (TS,),
        )
        conn.execute(
            """
            INSERT INTO tax_advances (taxpayer_nip, period_month, regime, advance_to_pay, created_at)
            VALUES ('1234563218', '2026-07', 'liniowy', 2280.00, ?)
            """,
            (TS,),
        )
        conn.commit()
    finally:
        conn.close()

    assert migrate.migrate(db_path) == [3]

    conn = migrate.get_connection(db_path)
    try:
        zus = conn.execute(
            "SELECT total_do_zaplaty, created_at, superseded_at FROM zus_declarations"
        ).fetchall()
        tax = conn.execute(
            "SELECT advance_to_pay, created_at, superseded_at FROM tax_advances"
        ).fetchall()
    finally:
        conn.close()

    assert zus == [(1646.47, TS, None)], "старый расчёт ZUS должен уцелеть и остаться действующим"
    assert tax == [(2280.00, TS, None)], "старая авансовая выплата должна уцелеть и остаться действующей"
