"""Читатель HTML-экспорта Telegram Desktop (`messages*.html`).

Экспорт этого канала сделан в HTML, а не в JSON — установлено переписью
(`probe/01_host_and_export.ps1`: `format: HTML`). Структура снята
разведчиком `probe/02_html_structure.py` с настоящего экспорта, без чтения
его содержимого:

    div.message default clearfix [joined]   — сообщение (joined = подряд
                                              идущее от того же отправителя)
    div.message service                     — служебное, пропускаем
    div.pull_right.date.details[title]      — "14.01.2025 10:12:00 UTC+01:00"
    a.media_file[href]                      — файл-вложение
    a.photo_wrap[href]                      — фото
    div.text                                — подпись (есть далеко не всегда)

Отдаёт те же `RawMessage`/`RawAttachment`, что и JSON-читатель, поэтому
`normalize.py` про формат источника ничего не знает и менять его не нужно.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator
from urllib.parse import unquote

from bs4 import BeautifulSoup

from segregator.ingest.export_reader import ExportPathError, RawAttachment, RawMessage

# Только корень экспорта: в files/ лежат .html-ВЛОЖЕНИЯ (в настоящем экспорте
# это присланное файлом React-приложение), а в ChatExport_*/ — отдельный,
# другой экспорт. Рекурсия здесь удвоила бы переписку.
PAGE_RE = re.compile(r"^messages\d*\.html$", re.IGNORECASE)
_MESSAGE_ID_RE = re.compile(r"(\d+)")
_DATE_RE = re.compile(
    r"(?P<d>\d{2})\.(?P<m>\d{2})\.(?P<y>\d{4})\s+"
    r"(?P<H>\d{2}):(?P<M>\d{2}):(?P<S>\d{2})\s*"
    r"UTC(?P<sign>[+-])(?P<oh>\d{2}):(?P<om>\d{2})"
)
# Схема URL — минимум два символа до двоеточия. Требование именно двух, а не
# одного, здесь несёт смысл: "C:/Windows/..." — это не схема, а windows-путь,
# и он обязан уйти в проверку выхода за корень экспорта, а не быть тихо
# пропущенным как «внешняя ссылка».
_EXTERNAL_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]+:")  # http:, https:, mailto:, data:


def export_pages(export_dir: Path) -> list[Path]:
    """Страницы экспорта в корне, в порядке messages.html, messages2.html, …"""
    export_dir = Path(export_dir)
    pages = [p for p in export_dir.iterdir() if p.is_file() and PAGE_RE.match(p.name)]
    if not pages:
        raise FileNotFoundError(
            f"В {export_dir} нет messages*.html. "
            "Если экспорт в JSON, разбирает ingest.export_reader."
        )
    return sorted(pages, key=_page_order)


def _page_order(path: Path) -> int:
    digits = re.findall(r"\d+", path.stem)
    return int(digits[0]) if digits else 1


def read_channel_name(export_dir: Path) -> str | None:
    """Имя канала из шапки первой страницы."""
    soup = _soup(export_pages(export_dir)[0])
    header = soup.select_one(".page_header .text")
    return header.get_text(strip=True) if header else None


def resolve_chat_id(export_dir: Path) -> int:
    """Стабильный chat_id для HTML-экспорта.

    В HTML-экспорте числового идентификатора чата нет вообще (в отличие от
    JSON, где он лежит в верхнеуровневом `id`). Поэтому он выводится из имени
    канала — детерминированно, иначе повторный прогон дал бы другой ключ и
    продублировал всю переписку.

    Значение отрицательное: настоящие Telegram-идентификаторы каналов сюда не
    попадут, и когда появится live-приём (Э6) с реальным chat_id, коллизии не
    будет — расхождение будет видно, а не замаскировано.
    """
    name = read_channel_name(export_dir) or str(Path(export_dir).name)
    digest = hashlib.sha256(name.encode("utf-8")).digest()
    return -int.from_bytes(digest[:6], "big")


def iter_messages_html(export_dir: Path) -> Iterator[RawMessage]:
    """Поток сообщений экспорта. Служебные записи пропускаются."""
    export_dir = Path(export_dir)
    for page in export_pages(export_dir):
        soup = _soup(page)
        for node in soup.select("div.message"):
            classes = node.get("class") or []
            if "service" in classes:
                continue  # вступления, закрепления, разделители дат
            message = _to_raw_message(node, export_dir)
            if message is not None:
                yield message


def _soup(path: Path) -> BeautifulSoup:
    return BeautifulSoup(path.read_text(encoding="utf-8", errors="replace"), "html.parser")


def _to_raw_message(node, export_dir: Path) -> RawMessage | None:
    message_id = _message_id(node)
    if message_id is None:
        return None

    attachments = []
    for link in node.select("a.media_file[href], a.photo_wrap[href]"):
        href = (link.get("href") or "").strip()
        if not href or _EXTERNAL_RE.match(href):
            # В настоящем экспорте есть ссылки на unpkg.com и mailto: —
            # это не вложения, а содержимое сообщений.
            continue
        resolved = _resolve_inside(export_dir, href)
        title = link.select_one(".title")
        attachments.append(
            RawAttachment(
                path=resolved,
                orig_name=title.get_text(strip=True) if title else None,
                mime=None,  # HTML-экспорт mime не сообщает, определим в Э3
                exists=resolved.is_file(),
            )
        )

    return RawMessage(
        message_id=message_id,
        sent_at=_sent_at(node, message_id),
        author=_text_of(node.select_one(".from_name")),
        body=_text_of(node.select_one("div.text")),
        attachments=attachments,
        raw="",  # исходный HTML в базу не кладём: это содержимое документа
    )


def _message_id(node) -> int | None:
    raw_id = node.get("id") or ""
    match = _MESSAGE_ID_RE.search(raw_id)
    return int(match.group(1)) if match else None


def _sent_at(node, message_id: int) -> str:
    date_node = node.select_one(".date[title]")
    title = date_node.get("title") if date_node else None
    match = _DATE_RE.search(title or "")
    if not match:
        # Строку даты в текст не выносим: она из экспорта, а исключение уходит
        # в stderr и в переписку при отладке (docs/DATA_BOUNDARY.md, инвариант 4).
        raise ValueError(
            f"Не разобрана дата сообщения {message_id}: формат не совпал с ожидаемым"
        )

    g = match.groupdict()
    offset_minutes = int(g["oh"]) * 60 + int(g["om"])
    if g["sign"] == "-":
        offset_minutes = -offset_minutes

    local = datetime(
        int(g["y"]), int(g["m"]), int(g["d"]),
        int(g["H"]), int(g["M"]), int(g["S"]),
    )
    # Telegram печатает местное время автора экспорта вместе со смещением;
    # в базе всё живёт в UTC, иначе месяцы разъедутся на границе суток.
    return (local - _minutes(offset_minutes)).replace(tzinfo=timezone.utc).isoformat()


def _minutes(n: int):
    from datetime import timedelta

    return timedelta(minutes=n)


def _text_of(node) -> str | None:
    if node is None:
        return None
    text = node.get_text("\n", strip=True)
    return text or None


def _resolve_inside(export_dir: Path, relative: str) -> Path:
    """Резолвить путь вложения строго внутри корня экспорта.

    Та же защита, что и в JSON-читателе: href приходит из данных и управляем
    не нами.

    href в HTML-экспорте процентно-кодирован (Telegram так пишет имена с
    пробелами и не-ASCII: `files/faktura%20nr%201.pdf`). Декодируем ДО резолва,
    иначе путь с литеральным `%20` не найдёт файл и вложение тихо уйдёт в
    «не найдено». Порядок важен: decode → resolve → проверка выхода за корень;
    `resolve()` нормализует `..`, так что `%2e%2e%2f` защиту не обходит.
    """
    decoded = unquote(relative)
    root = export_dir.resolve()
    candidate = (root / decoded).resolve()
    if not candidate.is_relative_to(root):
        # Сам путь в текст не выносим — это имя файла из экспорта.
        raise ExportPathError("Путь вложения ведёт за пределы корня экспорта")
    return candidate
