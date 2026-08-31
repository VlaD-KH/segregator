"""Потоковый читатель экспорта Telegram Desktop (`result.json`).

Читает через `ijson`, а не `json.load`: экспорт за год легко перевалит за
сотни мегабайт, а машина живёт на 6.9 ГБ RAM (ТЗ §04, приём).

Модуль намеренно ничего не знает ни про базу, ни про хранилище блобов —
он только превращает экспорт в поток `RawMessage`. Live-приём (Э6) отдаст
такие же объекты, и дальше по конвейеру оба входа неразличимы.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import ijson

RESULT_JSON = "result.json"

# Telegram подставляет это вместо пути, когда медиа не выгружено в экспорт.
# Строка не является путём, и попытка её резолвить создала бы мусорный файл.
_NOT_INCLUDED_MARKERS = (
    "(File not included",
    "(Photo not included",
    "(Video not included",
    "(Sticker not included",
)


class ExportPathError(ValueError):
    """Путь вложения уводит за пределы корня экспорта."""


@dataclass(frozen=True)
class RawAttachment:
    path: Path
    orig_name: str | None
    mime: str | None
    exists: bool


@dataclass(frozen=True)
class RawMessage:
    message_id: int
    sent_at: str  # ISO-8601 UTC
    author: str | None
    body: str | None
    attachments: list[RawAttachment]
    raw: str  # исходный JSON сообщения, для audit trail


def read_chat_id(export_dir: Path) -> int:
    """Верхнеуровневый `id` экспорта — он же chat_id в схеме БД."""
    path = _result_path(export_dir)
    with path.open("rb") as fh:
        for chat_id in ijson.items(fh, "id"):
            return int(chat_id)
    raise ValueError(f"В {RESULT_JSON} нет верхнеуровневого поля id")


def iter_messages(export_dir: Path) -> Iterator[RawMessage]:
    """Поток сообщений экспорта. Служебные записи пропускаются."""
    export_dir = Path(export_dir)
    path = _result_path(export_dir)

    with path.open("rb") as fh:
        for item in ijson.items(fh, "messages.item"):
            if item.get("type") != "message":
                continue  # service: вступления, закрепления — не документы
            yield _to_raw_message(item, export_dir)


def _result_path(export_dir: Path) -> Path:
    path = Path(export_dir) / RESULT_JSON
    if not path.is_file():
        raise FileNotFoundError(
            f"В каталоге EXPORT_DIR не найден {RESULT_JSON}. "
            "Если экспорт сделан в HTML, разбор устроен иначе — см. SPEC.md."
        )
    return path


def _to_raw_message(item: dict, export_dir: Path) -> RawMessage:
    attachments = []
    for key in ("file", "photo"):
        rel = item.get(key)
        if not isinstance(rel, str) or not rel:
            continue
        if rel.startswith(_NOT_INCLUDED_MARKERS):
            continue
        resolved = _resolve_inside(export_dir, rel)
        attachments.append(
            RawAttachment(
                path=resolved,
                orig_name=item.get("file_name"),
                mime=item.get("mime_type"),
                exists=resolved.is_file(),
            )
        )

    return RawMessage(
        message_id=int(item["id"]),
        sent_at=_to_iso_utc(item),
        author=item.get("from"),
        body=_to_text(item),
        attachments=attachments,
        raw=_compact_json(item),
    )


def _resolve_inside(export_dir: Path, relative: str) -> Path:
    """Резолвить путь вложения строго внутри корня экспорта.

    Путь приходит из данных, то есть управляем не нами: `../../..` в поле
    `file` иначе увёл бы чтение и запись куда угодно по файловой системе.

    **`unquote` здесь НЕ вызывается, и это не упущение.** Telegram пишет в
    `result.json` литеральные относительные пути (`"file": "files/doc_a.pdf"`),
    без процентного кодирования — в отличие от href в HTML-экспорте, где оно
    есть и где `html_reader._resolve_inside` декодирует. Добавить decode сюда
    значило бы испортить имя файла, в котором стоит настоящий знак процента.
    """
    root = export_dir.resolve()
    try:
        candidate = (root / relative).resolve()
    except (OSError, ValueError) as error:
        # `resolve()` ходит в файловую систему и на враждебном вводе падает не
        # только «не найдено»: JSON умеет нести `"file": "a\x00.pdf"`, и NUL
        # в пути даёт `ValueError: embedded null character`; слишком длинный
        # путь на NTFS — `OSError`. Голое исключение отсюда прошло бы мимо
        # ExportPathError и мимо обработчиков в cli.backfill, убив весь прогон,
        # а его traceback вынес бы наружу имя файла. Контракт функции: либо
        # безопасный путь внутри корня, либо ExportPathError. Причину не
        # прикладываем (`from None`) — сообщение системы содержит сам путь.
        reason = type(error).__name__
        raise ExportPathError(
            f"Путь вложения не удалось разобрать: {reason}"
        ) from None
    if not candidate.is_relative_to(root):
        # Сам путь в текст не выносим — это имя файла из экспорта
        # (docs/DATA_BOUNDARY.md, инвариант 1).
        raise ExportPathError("Путь вложения ведёт за пределы корня экспорта")
    return candidate


def _to_iso_utc(item: dict) -> str:
    # Идентификатор связываем отдельно: в текст исключения попадает только он,
    # а не `item` — весь сырой словарь сообщения с текстом и контрагентом.
    # Инвариант в тестах разрешает в `raise` строго перечисленные имена.
    message_id = item.get("id")
    unixtime = item.get("date_unixtime")
    if unixtime is not None:
        return datetime.fromtimestamp(int(unixtime), tz=timezone.utc).isoformat()
    # Запасной путь: "date" без зоны — трактуем как UTC, чтобы поле не пустовало.
    raw_date = item.get("date")
    if raw_date:
        try:
            return datetime.fromisoformat(raw_date).replace(tzinfo=timezone.utc).isoformat()
        except (TypeError, ValueError):
            # `fromisoformat` вкладывает разобранную строку прямо в текст
            # ошибки: `Invalid isoformat string: 'FAKTURA-KOWALSKI-2025'`.
            # Это поле документа, и отсюда оно уехало бы в stderr и в
            # переписку. Тот же дефект, что чинился в `html_reader._sent_at`;
            # JSON-ветка его сохраняла.
            raise ValueError(
                f"Не разобрана дата сообщения {message_id}: "
                "формат не совпал с ожидаемым"
            ) from None
    raise ValueError(f"У сообщения {message_id} нет ни date_unixtime, ни date")


def _to_text(item: dict) -> str | None:
    """Собрать текст сообщения из text/text_entities."""
    text = item.get("text")
    if isinstance(text, str):
        return text or None
    # text может быть списком кусков (ссылки, форматирование) — склеиваем.
    if isinstance(text, list):
        parts = [p if isinstance(p, str) else str(p.get("text", "")) for p in text]
        joined = "".join(parts)
        return joined or None
    return None


def _compact_json(item: dict) -> str:
    import json

    return json.dumps(item, ensure_ascii=False, separators=(",", ":"))
