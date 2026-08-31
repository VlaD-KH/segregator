"""Content-addressed хранилище вложений.

Имя файла — его sha256, поэтому побайтовый дубль физически не может занять
второе место на диске: повторная отправка того же документа создаёт вторую
ссылку в `attachments`, но не вторую копию (ТЗ §04, нормализация).
"""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path

BLOBS_DIR = "blobs"
_CHUNK = 1024 * 1024  # читаем мегабайтами: файл может быть больше памяти


@dataclass(frozen=True)
class BlobRef:
    """Результат укладки файла в хранилище."""

    sha256: str
    path: Path
    was_new: bool


def sha256_of(path: Path) -> str:
    """Посчитать sha256 файла, не загружая его целиком в память.

    Ошибки файловой системы переписываются: `open()` вкладывает в текст
    `OSError` полный путь (`[Errno 2] No such file or directory: 'C:/…/
    tajna-faktura.pdf'`), а `normalize._ingest_attachment` зовёт эту функцию
    напрямую в сухом прогоне — путь уехал бы в stderr вместе с именем
    документа. Инвариант в тестах такое не ловит: `raise` тут не наш.
    """
    digest = hashlib.sha256()
    try:
        with path.open("rb") as fh:
            while chunk := fh.read(_CHUNK):
                digest.update(chunk)
    except OSError as error:
        reason = type(error).__name__
        raise FileNotFoundError(f"Не прочитан файл вложения: {reason}") from None
    return digest.hexdigest()


def blob_relative_path(sha256: str, suffix: str) -> Path:
    """Путь блоба относительно ARCHIVE_DIR. Чистая функция, к диску не ходит.

    Двухуровневый фанаут `ab/cd/` — чтобы в одном каталоге не оказалось
    десятков тысяч файлов, на чём проседают и NTFS, и ext4.
    """
    return Path(BLOBS_DIR) / sha256[:2] / sha256[2:4] / f"{sha256}{suffix}"


def store_blob(archive_dir: Path, source: Path) -> BlobRef:
    """Положить файл в хранилище. Идемпотентно по содержимому."""
    if not source.is_file():
        # Путь к документу в текст не выносим — исключение попадает в stderr
        # и в переписку при отладке (docs/DATA_BOUNDARY.md, инвариант 1).
        raise FileNotFoundError("Файл вложения не найден на диске")

    digest = sha256_of(source)
    target = Path(archive_dir) / blob_relative_path(digest, source.suffix)

    if target.exists():
        # Тот же sha256 — тот же файл байт в байт. Копию не делаем;
        # вызывающий по was_new=False поймёт, что нужна только вторая ссылка.
        return BlobRef(sha256=digest, path=target, was_new=False)

    target.parent.mkdir(parents=True, exist_ok=True)
    # Через временный файл: если прогон оборвётся на середине копирования,
    # в хранилище не должно остаться обрезанного блоба под валидным именем.
    tmp = target.with_name(target.name + ".part")
    try:
        shutil.copyfile(source, tmp)
        tmp.replace(target)
    finally:
        tmp.unlink(missing_ok=True)

    return BlobRef(sha256=digest, path=target, was_new=True)
