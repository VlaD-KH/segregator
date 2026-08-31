"""CLI-точки входа: `segregator init`, `segregator doctor`."""

from __future__ import annotations

import platform
import shutil
import sys

import httpx
import typer
from pydantic import ValidationError

from segregator import logging as slog
from segregator import paths
from segregator.config import Settings, format_validation_error
from segregator.db import migrate


def _ensure_utf8_console() -> None:
    # Консольная кодовая страница Windows не гарантированно содержит
    # кириллицу (например, польская региональная локаль даёт cp1250) —
    # без этого русский вывод CLI падает с UnicodeEncodeError.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


_ensure_utf8_console()

app = typer.Typer(add_completion=False, help="Segregator — локальный бухгалтерский архиватор")


def _load_settings_or_exit() -> Settings:
    try:
        return Settings()
    except ValidationError as error:
        typer.echo(format_validation_error(error))
        raise typer.Exit(code=1)


@app.command()
def init() -> None:
    """Создать базу данных и корневой скелет дерева архива."""
    settings = _load_settings_or_exit()
    slog.configure_logging(settings.archive_dir / "logs")
    log = slog.get_logger(__name__)

    applied = migrate.migrate(settings.archive_dir / "segregator.db")
    created = paths.ensure_tree(settings)

    typer.echo(f"База данных: применено новых миграций — {len(applied)}")
    typer.echo(f"Дерево папок: создано новых директорий — {len(created)}")
    log.info("init.completed", migrations_applied=len(applied), dirs_created=len(created))


@app.command()
def backfill(
    dry_run: bool = typer.Option(False, "--dry-run", help="Посчитать, но ничего не записывать"),
    limit: int | None = typer.Option(None, "--limit", help="Разобрать только первые N сообщений"),
) -> None:
    """Разобрать исторический экспорт Telegram Desktop в архив."""
    from segregator.ingest.export_reader import ExportPathError
    from segregator.ingest.normalize import backfill as run_backfill

    settings = _load_settings_or_exit()
    slog.configure_logging(settings.archive_dir / "logs")

    try:
        stats = run_backfill(
            settings.archive_dir,
            settings.export_dir,
            dry_run=dry_run,
            limit=limit,
        )
    except FileNotFoundError as error:
        typer.echo(f"Экспорт не найден: {error}")
        raise typer.Exit(code=1)
    except ExportPathError as error:
        # Путь из данных увёл за пределы экспорта — это не «плохой файл»,
        # а повод остановиться и посмотреть, что за экспорт нам подсунули.
        typer.echo(f"Экспорт отклонён: {error}")
        raise typer.Exit(code=1)

    typer.echo(f"Сообщений:  добавлено {stats.messages_added}, уже было {stats.messages_skipped}")
    typer.echo(f"Вложений:   добавлено {stats.attachments_added}, уже было {stats.attachments_skipped}")
    typer.echo(f"Блобов:     новых {stats.blobs_new}, дублей {stats.blobs_deduped}")
    if stats.missing_files:
        typer.echo(f"Пропущено:  {stats.missing_files} вложений не найдено на диске")
    if dry_run:
        typer.echo("Сухой прогон — ничего не записано.")


def _check_tesseract() -> tuple[bool, str, str]:
    path = shutil.which("tesseract")
    if path:
        return True, "tesseract", path
    return False, "tesseract", "не найден в PATH"


def _check_llm(settings: Settings) -> tuple[bool, str, str]:
    url = f"{settings.llm_base_url.rstrip('/')}/models"
    try:
        response = httpx.get(url, timeout=2.0)
        response.raise_for_status()
        return True, "LLM", f"{settings.llm_base_url} отвечает"
    except (httpx.ConnectError, httpx.TimeoutException):
        return False, "LLM", f"{settings.llm_base_url} недоступен (сервер не запущен?)"
    except httpx.HTTPStatusError as error:
        return False, "LLM", f"{settings.llm_base_url} вернул {error.response.status_code}"


def _check_writable(label: str, target: object) -> tuple[bool, str, str]:
    import os
    from pathlib import Path

    path = Path(target)
    check_path = path if path.exists() else path.parent
    if check_path.exists() and os.access(check_path, os.W_OK):
        return True, label, str(path)
    return False, label, f"{path} — нет прав на запись"


def _check_readable(label: str, target: object) -> tuple[bool, str, str]:
    from pathlib import Path

    path = Path(target)
    if path.is_dir():
        return True, label, str(path)
    return False, label, f"{path} — не найден или не папка"


@app.command()
def doctor() -> None:
    """Проверить окружение: tesseract, доступность LLM, права на папки."""
    settings = _load_settings_or_exit()

    checks = [
        _check_tesseract(),
        _check_llm(settings),
        _check_readable("EXPORT_DIR", settings.export_dir),
        _check_writable("ARCHIVE_DIR", settings.archive_dir),
    ]

    for ok, label, detail in checks:
        typer.echo(f"[{'OK' if ok else 'FAIL'}] {label}: {detail}")

    is_wsl = "microsoft" in platform.uname().release.lower()
    typer.echo(f"[INFO] WSL2: {'да' if is_wsl else 'нет — реальное дерево должно жить в WSL2 (ТЗ §02)'}")

    if not all(ok for ok, *_ in checks):
        raise typer.Exit(code=1)
