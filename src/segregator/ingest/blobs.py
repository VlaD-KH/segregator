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
    """Посчитать sha256 файла, не загружая его целиком в память."""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(_CHUNK):
            digest.update(chunk)
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
        raise FileNotFoundError(f"Файл вложения не найден: {source}")

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
