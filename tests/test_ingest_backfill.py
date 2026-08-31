"""Т3 — нормализация экспорта в БД: счётчики, дедуп, идемпотентность."""

from __future__ import annotations

from pathlib import Path

import pytest

from segregator.db import migrate
from segregator.ingest.normalize import BackfillStats, backfill

FIXTURE = Path(__file__).parent / "fixtures" / "export_min"


@pytest.fixture
def archive(tmp_path):
    """Готовый архив с применёнными миграциями."""
    archive_dir = tmp_path / "archive"
    migrate.migrate(archive_dir / "segregator.db")
    return archive_dir


def _counts(archive_dir: Path) -> dict[str, int]:
    conn = migrate.get_connection(archive_dir / "segregator.db")
    try:
        return {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("messages", "blobs", "attachments")
        }
    finally:
        conn.close()


def test_backfill_counts_match_the_export(archive):
    stats = backfill(archive, FIXTURE)

    assert isinstance(stats, BackfillStats)
    # 6 записей в фикстуре, одна служебная → 5 сообщений.
    assert stats.messages_added == 5
    # 5 ссылок на вложения, одна указывает на несуществующий файл.
    assert stats.attachments_added == 4
    assert stats.missing_files == 1

    assert _counts(archive) == {"messages": 5, "blobs": 3, "attachments": 4}


def test_identical_file_is_stored_once_but_linked_twice(archive):
    backfill(archive, FIXTURE)

    conn = migrate.get_connection(archive / "segregator.db")
    try:
        # doc_a.pdf отправлен в сообщениях 1 и 4 — один блоб, две ссылки.
        rows = conn.execute(
            "SELECT sha256, COUNT(*) FROM attachments GROUP BY sha256 ORDER BY 2 DESC"
        ).fetchall()
    finally:
        conn.close()

    assert rows[0][1] == 2, "один и тот же файл должен дать две записи attachments"
    assert len(rows) == 3, "различных блобов должно быть три"

    stored = list((archive / "blobs").rglob("*"))
    stored_files = [p for p in stored if p.is_file()]
    assert len(stored_files) == 3, "на диске ровно три файла, без второй копии"


def test_second_run_is_a_no_op(archive):
    backfill(archive, FIXTURE)
    before = _counts(archive)
    files_before = sorted(p.name for p in (archive / "blobs").rglob("*") if p.is_file())

    stats = backfill(archive, FIXTURE)

    # Прямая приёмка ТЗ §13/Э2: повторный прогон не создаёт ничего нового.
    assert stats.messages_added == 0
    assert stats.attachments_added == 0
    assert stats.blobs_new == 0
    assert stats.messages_skipped == 5

    assert _counts(archive) == before
    files_after = sorted(p.name for p in (archive / "blobs").rglob("*") if p.is_file())
    assert files_after == files_before


def test_dry_run_writes_nothing(archive):
    stats = backfill(archive, FIXTURE, dry_run=True)

    # Счётчики считаются как если бы писали...
    assert stats.messages_added == 5
    assert stats.attachments_added == 4
    # ...но ни одной строки и ни одного файла на диске нет.
    assert _counts(archive) == {"messages": 0, "blobs": 0, "attachments": 0}
    assert not (archive / "blobs").exists() or not list((archive / "blobs").rglob("*.pdf"))


def test_dry_run_predicts_the_real_run_exactly(archive, tmp_path):
    """Сухой прогон обязан предсказывать боевой, иначе он бесполезен.

    Ловит ошибку, при которой дедупликация в dry-run не учитывалась: файл,
    встреченный дважды, считался новым оба раза, потому что на диск ничего
    не пишется и коллидировать не с чем.
    """
    predicted = backfill(archive, FIXTURE, dry_run=True)
    actual = backfill(archive, FIXTURE)

    assert predicted.as_dict() == actual.as_dict()


def test_limit_stops_early(archive):
    stats = backfill(archive, FIXTURE, limit=2)

    assert stats.messages_added == 2
    assert _counts(archive)["messages"] == 2


def test_message_rows_carry_source_and_timestamp(archive):
    backfill(archive, FIXTURE)

    conn = migrate.get_connection(archive / "segregator.db")
    try:
        row = conn.execute(
            "SELECT chat_id, message_id, sent_at, source, body FROM messages WHERE message_id = 1"
        ).fetchone()
    finally:
        conn.close()

    chat_id, message_id, sent_at, source, body = row
    assert chat_id == 1900000001
    assert message_id == 1
    assert sent_at.startswith("2025-01-14T")
    assert source == "export"
    assert body == "faktura styczen"


def test_blob_rows_record_size_and_stored_path(archive):
    backfill(archive, FIXTURE)

    conn = migrate.get_connection(archive / "segregator.db")
    try:
        rows = conn.execute("SELECT sha256, bytes, stored_path FROM blobs").fetchall()
    finally:
        conn.close()

    for sha256, size, stored_path in rows:
        assert len(sha256) == 64
        assert size > 0
        # Путь хранится относительным — архив должен переезжать целиком.
        assert not Path(stored_path).is_absolute()
        assert (archive / stored_path).is_file()
