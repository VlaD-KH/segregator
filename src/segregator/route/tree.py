"""Два дерева архива и польские имена месяцев (TASK-004).

F-5.1: дерево по дате документа
F-5.2: дерево по дате платежа (и папка _bez-daty-platnosci)
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from segregator.paths import NO_PAYMENT_DATE_DIR
from segregator.route.naming import safe_filename

_MONTH_NAMES = {
    1: "01-styczen",
    2: "02-luty",
    3: "03-marzec",
    4: "04-kwiecien",
    5: "05-maj",
    6: "06-czerwiec",
    7: "07-lipiec",
    8: "08-sierpien",
    9: "09-wrzesien",
    10: "10-pazdziernik",
    11: "11-listopad",
    12: "12-grudzien",
}


def month_folder(month: int) -> str:
    """Возвращает польское имя месяца с числовым префиксом (01-styczen ... 12-grudzien).

    Вне диапазона 1..12 поднимает ValueError.
    """
    if month not in _MONTH_NAMES:
        raise ValueError("Недопустимый номер месяца: ожидается от 1 до 12")
    return _MONTH_NAMES[month]


def document_tree_path(archive_dir: Path, doc_date: date, category: str, filename: str) -> Path:
    """Путь в дереве архива по дате документа:
    {archive_dir}/archiwum/wg-daty-dokumentu/{YYYY}/{MM-miesiac}/{kategoria}/{plik}
    """
    safe_cat = safe_filename(category)
    safe_file = safe_filename(filename)
    return (
        Path(archive_dir)
        / "archiwum"
        / "wg-daty-dokumentu"
        / str(doc_date.year)
        / month_folder(doc_date.month)
        / safe_cat
        / safe_file
    )


def payment_tree_path(archive_dir: Path, paid_date: date | None, category: str, filename: str) -> Path:
    """Путь в дереве архива по дате платежа:
    {archive_dir}/archiwum/wg-daty-platnosci/{YYYY}/{MM-miesiac}/{kategoria}/{plik}
    Если paid_date is None:
    {archive_dir}/archiwum/wg-daty-platnosci/{NO_PAYMENT_DATE_DIR}/{plik}
    """
    safe_file = safe_filename(filename)
    base = Path(archive_dir) / "archiwum" / "wg-daty-platnosci"
    if paid_date is None:
        return base / NO_PAYMENT_DATE_DIR / safe_file

    safe_cat = safe_filename(category)
    return (
        base
        / str(paid_date.year)
        / month_folder(paid_date.month)
        / safe_cat
        / safe_file
    )
