"""Выбор читателя экспорта по тому, что реально лежит в каталоге.

Telegram Desktop выгружает переписку либо в JSON (`result.json`), либо в HTML
(`messages.html`, `messages2.html`, …). Формат задаётся при выгрузке и
постфактум не меняется. Реальный экспорт канала «бухгалтерия» — в HTML
(установлено переписью Э0), синтетические фикстуры Э2 — в JSON; поддерживаются
оба.

Это единственное место в конвейере, где форматы различаются. `normalize.py`
импортирует `iter_messages` / `read_chat_id` отсюда и про формат источника
ничего не знает — бэкфилл (Э2) и live-приём (Э6) дальше неразличимы.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

from segregator.ingest import export_reader, html_reader
from segregator.ingest.export_reader import (
    ExportPathError,
    RawAttachment,
    RawMessage,
)

__all__ = [
    "ExportPathError",
    "RawAttachment",
    "RawMessage",
    "detect_format",
    "iter_messages",
    "read_chat_id",
]

JSON = "json"
HTML = "html"


def detect_format(export_dir: Path) -> str:
    """`"json"`, если в корне есть `result.json`; `"html"`, если есть
    `messages*.html`. Иначе — `FileNotFoundError`.

    `result.json` проверяется первым: если человек положил рядом обе выгрузки,
    JSON разбирается точнее (в нём есть числовой chat_id и mime вложений).
    """
    export_dir = Path(export_dir)
    if not export_dir.is_dir():
        raise FileNotFoundError(f"Каталог экспорта не найден: {export_dir}")

    if (export_dir / export_reader.RESULT_JSON).is_file():
        return JSON
    if any(
        p.is_file() and html_reader.PAGE_RE.match(p.name)
        for p in export_dir.iterdir()
    ):
        return HTML

    raise FileNotFoundError(
        f"В {export_dir} нет ни {export_reader.RESULT_JSON}, ни messages*.html — "
        "не похоже на экспорт Telegram Desktop."
    )


def iter_messages(export_dir: Path) -> Iterator[RawMessage]:
    """Поток `RawMessage` из экспорта — форматом занимается `detect_format`."""
    if detect_format(export_dir) == JSON:
        yield from export_reader.iter_messages(export_dir)
    else:
        yield from html_reader.iter_messages_html(export_dir)


def read_chat_id(export_dir: Path) -> int:
    """Стабильный `chat_id` экспорта. Для JSON — верхнеуровневый `id`; для HTML
    он выводится из имени канала (числового id в HTML-выгрузке нет)."""
    if detect_format(export_dir) == JSON:
        return export_reader.read_chat_id(export_dir)
    return html_reader.resolve_chat_id(export_dir)
