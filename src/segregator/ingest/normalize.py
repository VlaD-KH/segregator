"""Нормализация: поток RawMessage → строки БД + блобы на диске.

Здесь сходятся оба входа конвейера. Бэкфилл (Э2) и live-приём (Э6) отдают
одинаковые `RawMessage`, поэтому дальше по конвейеру документ из истории и
документ, прилетевший минуту назад, неразличимы (ТЗ §04).

Идемпотентность держится на естественных ключах самой схемы —
`UNIQUE(chat_id, message_id)` и `UNIQUE(message_id, idx)` — а не на
предварительных SELECT: так повторный прогон безопасен даже при гонке.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from segregator.db import migrate
from segregator.ingest.blobs import blob_relative_path, sha256_of, store_blob
from segregator.ingest.export_reader import RawMessage, iter_messages, read_chat_id
from segregator.logging import get_logger

log = get_logger(__name__)

SOURCE_EXPORT = "export"


@dataclass
class BackfillStats:
    """Счётчики прогона. Только числа — ни имён файлов, ни содержимого."""

    messages_added: int = 0
    messages_skipped: int = 0
    attachments_added: int = 0
    attachments_skipped: int = 0
    blobs_new: int = 0
    blobs_deduped: int = 0
    missing_files: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "messages_added": self.messages_added,
            "messages_skipped": self.messages_skipped,
            "attachments_added": self.attachments_added,
            "attachments_skipped": self.attachments_skipped,
            "blobs_new": self.blobs_new,
            "blobs_deduped": self.blobs_deduped,
            "missing_files": self.missing_files,
        }


def backfill(
    archive_dir: Path,
    export_dir: Path,
    *,
    dry_run: bool = False,
    limit: int | None = None,
) -> BackfillStats:
    """Разобрать экспорт в архив. Повторный вызов ничего не дублирует.

    Весь прогон идёт одной транзакцией: при обрыве база откатывается целиком,
    а блобы остаются на диске. Это не рассинхрон, а самолечение — следующий
    прогон перезапишет строки и переиспользует уже уложенные блобы по sha256.
    """
    archive_dir = Path(archive_dir)
    export_dir = Path(export_dir)

    chat_id = read_chat_id(export_dir)
    stats = BackfillStats()
    # Блобы, уже учтённые в этом прогоне. Нужны сухому прогону: он ничего не
    # пишет, поэтому без этого множества файл, встреченный дважды, считался бы
    # новым оба раза — и предсказание разошлось бы с боевым прогоном.
    seen_digests: set[str] = set()

    conn = migrate.get_connection(archive_dir / "segregator.db")
    try:
        for index, message in enumerate(iter_messages(export_dir)):
            if limit is not None and index >= limit:
                break
            _ingest_message(
                conn, archive_dir, chat_id, message, stats, dry_run, seen_digests
            )
        if dry_run:
            conn.rollback()
        else:
            conn.commit()
    finally:
        conn.close()

    log.info("backfill.completed", dry_run=dry_run, **stats.as_dict())
    return stats


def _ingest_message(
    conn,
    archive_dir: Path,
    chat_id: int,
    message: RawMessage,
    stats: BackfillStats,
    dry_run: bool,
    seen_digests: set[str],
) -> None:
    cursor = conn.execute(
        """
        INSERT INTO messages (chat_id, message_id, sent_at, author, body, source, raw)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(chat_id, message_id) DO NOTHING
        """,
        (chat_id, message.message_id, message.sent_at, message.author,
         message.body, SOURCE_EXPORT, message.raw),
    )

    if cursor.rowcount:
        stats.messages_added += 1
        row_id = cursor.lastrowid
    else:
        stats.messages_skipped += 1
        row_id = conn.execute(
            "SELECT id FROM messages WHERE chat_id = ? AND message_id = ?",
            (chat_id, message.message_id),
        ).fetchone()[0]

    for idx, attachment in enumerate(message.attachments):
        if not attachment.exists:
            # Экспорт бывает неполным — медиа не выгрузилось. Это не повод
            # ронять прогон: считаем пропуск и идём дальше (ТЗ §14, риски).
            stats.missing_files += 1
            log.warning(
                "backfill.attachment_missing",
                message_id=message.message_id,
                idx=idx,
            )
            continue

        _ingest_attachment(
            conn, archive_dir, row_id, idx, attachment, stats, dry_run, seen_digests
        )


def _ingest_attachment(
    conn, archive_dir: Path, row_id: int, idx: int, attachment, stats: BackfillStats,
    dry_run: bool, seen_digests: set[str],
) -> None:
    if dry_run:
        # В сухом прогоне файл не копируем — только считаем то, что посчитали бы.
        digest = sha256_of(attachment.path)
        stored_path = blob_relative_path(digest, attachment.path.suffix)
        was_new = digest not in seen_digests and not (archive_dir / stored_path).exists()
    else:
        ref = store_blob(archive_dir, attachment.path)
        digest, was_new = ref.sha256, ref.was_new
        stored_path = ref.path.relative_to(archive_dir)

    seen_digests.add(digest)

    if was_new:
        stats.blobs_new += 1
    else:
        stats.blobs_deduped += 1

    conn.execute(
        """
        INSERT INTO blobs (sha256, bytes, mime, stored_path)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(sha256) DO NOTHING
        """,
        (digest, attachment.path.stat().st_size, attachment.mime,
         stored_path.as_posix()),
    )

    cursor = conn.execute(
        """
        INSERT INTO attachments (message_id, idx, sha256, orig_name)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(message_id, idx) DO NOTHING
        """,
        (row_id, idx, digest, attachment.orig_name),
    )
    if cursor.rowcount:
        stats.attachments_added += 1
    else:
        stats.attachments_skipped += 1
