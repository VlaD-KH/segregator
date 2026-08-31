"""Т4 — CLI `segregator backfill`."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from segregator.cli import app
from segregator.db import migrate

FIXTURE = Path(__file__).parent / "fixtures" / "export_min"
runner = CliRunner()


@pytest.fixture
def project(isolated_project, monkeypatch):
    """isolated_project, но EXPORT_DIR указывает на синтетический экспорт."""
    monkeypatch.setenv("EXPORT_DIR", str(FIXTURE))
    archive_dir = isolated_project["archive_dir"]
    migrate.migrate(archive_dir / "segregator.db")
    return isolated_project


def _row_count(archive_dir: Path, table: str) -> int:
    conn = migrate.get_connection(archive_dir / "segregator.db")
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        conn.close()


def test_backfill_reports_counts_in_russian(project):
    result = runner.invoke(app, ["backfill"])

    assert result.exit_code == 0, result.output
    assert "Сообщений" in result.output
    assert "Вложений" in result.output
    assert _row_count(project["archive_dir"], "messages") == 5
    assert _row_count(project["archive_dir"], "attachments") == 4


def test_backfill_dry_run_writes_nothing(project):
    result = runner.invoke(app, ["backfill", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "ничего не записано" in result.output.lower()
    assert _row_count(project["archive_dir"], "messages") == 0


def test_backfill_second_run_reports_zeros(project):
    runner.invoke(app, ["backfill"])
    result = runner.invoke(app, ["backfill"])

    assert result.exit_code == 0, result.output
    # Идемпотентность видна пользователю, а не только в тестах.
    assert "добавлено 0" in result.output.lower()
    assert _row_count(project["archive_dir"], "messages") == 5


def test_backfill_limit_is_respected(project):
    result = runner.invoke(app, ["backfill", "--limit", "2"])

    assert result.exit_code == 0, result.output
    assert _row_count(project["archive_dir"], "messages") == 2


def test_backfill_reports_missing_attachments(project):
    result = runner.invoke(app, ["backfill"])

    # В фикстуре одно вложение отсутствует на диске — пользователь должен узнать.
    assert "пропущено" in result.output.lower() or "не найден" in result.output.lower()


def test_backfill_does_not_leak_filenames_into_output(project):
    result = runner.invoke(app, ["backfill"])

    # Сначала убеждаемся, что отчёт вообще непустой и содержательный — иначе
    # проверка на утечку прошла бы вхолостую на пустом выводе.
    assert result.exit_code == 0, result.output
    assert "добавлено 5" in result.output

    # Контур данных: в отчёт идут счётчики, не имена документов.
    for leaked in ("faktura-a.pdf", "doc_a.pdf", "brakujacy.pdf", "photo_1.jpg",
                   "faktura styczen", "paragon"):
        assert leaked not in result.output
