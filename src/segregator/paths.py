"""Минимальный скелет дерева архива.

Полная матрица год×месяц×категория сюда сознательно не входит: список
категорий в ТЗ §06 сам помечен как заглушка до Э0. Год/месяц/категория
создаются по факту первого документа в раскладке (Э5).
"""

from __future__ import annotations

from pathlib import Path

from segregator.config import Settings

NO_PAYMENT_DATE_DIR = "_bez-daty-platnosci"


def ensure_tree(settings: Settings) -> list[Path]:
    """Создать корневой скелет дерева и служебные папки. Возвращает список созданных."""
    archive = settings.archive_dir
    by_doc = archive / "archiwum" / "wg-daty-dokumentu"
    by_pay = archive / "archiwum" / "wg-daty-platnosci"

    created: list[Path] = []

    def _mk(path: Path) -> None:
        if path.exists():
            if not path.is_dir():
                raise NotADirectoryError(f"Путь занят не-папкой: {path}")
            return
        path.mkdir(parents=True, exist_ok=True)
        created.append(path)

    for directory in (
        by_doc,
        by_pay,
        by_pay / NO_PAYMENT_DATE_DIR,
        archive / "blobs",
        archive / "rejestry",
        archive / "logs",
    ):
        _mk(directory)

    return created
