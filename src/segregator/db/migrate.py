"""Нумерованные миграции схемы: применяются по порядку, идемпотентно."""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
_NAME_RE = re.compile(r"^(\d{4})_([a-zA-Z0-9_]+)\.sql$")


def get_connection(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _discover() -> list[tuple[int, str, Path]]:
    migrations: list[tuple[int, str, Path]] = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        match = _NAME_RE.match(path.name)
        if not match:
            raise ValueError(f"Некорректное имя файла миграции: {path.name}")
        migrations.append((int(match.group(1)), match.group(2), path))
    return migrations


def migrate(db_path: Path) -> list[int]:
    """Применить неприменённые миграции. Возвращает версии, применённые в этом вызове."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version    INTEGER PRIMARY KEY,
                name       TEXT NOT NULL,
                applied_at TEXT NOT NULL
            )
            """
        )
        conn.commit()

        already_applied = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
        applied_now: list[int] = []
        for version, name, path in _discover():
            if version in already_applied:
                continue
            conn.executescript(path.read_text(encoding="utf-8"))
            conn.execute(
                "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
                (version, name, datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
            applied_now.append(version)
        return applied_now
    finally:
        conn.close()
