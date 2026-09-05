"""Связывание файлов в архиве: hardlink, откат на копию через границу тома, перенос ссылок (TASK-003).

F-5.3: hardlink вместо копии
F-5.4: откат на копию с логированием EXDEV/тома
F-5.6: перенос ссылки без дублирования (relink)
"""

from __future__ import annotations

import errno
import logging
import os
from pathlib import Path
import shutil

logger = logging.getLogger(__name__)


def link_or_copy(src: Path, dst: Path) -> str:
    """Создаёт hardlink от src к dst или копирует файл при пересечении границы тома (EXDEV).

    Возвращает "hardlink" или "copy".
    """
    src_path = Path(src)
    dst_path = Path(dst)

    if not src_path.exists():
        raise FileNotFoundError("Исходный файл не найден")

    if dst_path.exists():
        raise FileExistsError("Файл назначения уже существует")

    dst_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        os.link(src_path, dst_path)
        return "hardlink"
    except OSError as exc:
        if exc.errno == errno.EXDEV:
            # Пути сюда не попадают. Имя файла в архиве собрано из данных
            # документа (дата, категория, контрагент, номер фактуры), а
            # инвариант 3 DATA_BOUNDARY.md не делает исключения для
            # предупреждений — ровно то, за что заведён дефект D2.
            # Диагностика даётся буквами тома: их достаточно, чтобы понять,
            # что дерево архива и blobs/ разъехались по разным дискам.
            logger.warning(
                "Невозможно создать hardlink через границу тома (EXDEV): "
                "источник на томе %s, назначение на томе %s. Откат на копирование — "
                "место на диске удвоится.",
                (src_path.drive or "?"),
                (dst_path.drive or "?"),
            )
            shutil.copy2(src_path, dst_path)
            return "copy"
        raise


def same_physical_file(a: Path, b: Path) -> bool:
    """Проверяет, ссылаются ли пути a и b на одну и ту же физическую запись в ФС (st_dev и st_ino)."""
    try:
        st_a = os.stat(a)
        st_b = os.stat(b)
    except (FileNotFoundError, OSError):
        return False
    return (st_a.st_dev == st_b.st_dev) and (st_a.st_ino == st_b.st_ino)


def relink(old: Path, new: Path) -> None:
    """Переносит физическую ссылку на файл в новое место, не дублируя её."""
    old_path = Path(old)
    new_path = Path(new)

    if not old_path.exists():
        raise FileNotFoundError("Исходная ссылка не найдена")

    if new_path.exists():
        raise FileExistsError("Файл назначения уже существует")

    new_path.parent.mkdir(parents=True, exist_ok=True)
    link_or_copy(old_path, new_path)
    old_path.unlink()
