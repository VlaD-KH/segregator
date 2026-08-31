import sqlite3

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
}


def test_migrate_creates_all_tables(tmp_path):
    db_path = tmp_path / "segregator.db"
    applied = migrate.migrate(db_path)
    assert applied == [1]

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
