"""
src/segregator/accounting/period.py
Агрегаты периода и сквозная нумерация KPiR.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
import re
import sqlite3

__all__ = ["PeriodTotals", "close_month", "next_lp"]

_PERIOD_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


@dataclass(frozen=True)
class PeriodTotals:
    period: str        # "YYYY-MM"
    przychody: Decimal
    koszty: Decimal
    dochod: Decimal    # przychody - koszty, может быть отрицательным
    entries: int


def next_lp(conn: sqlite3.Connection, entry_date: date) -> int:
    """Следующая liczba porządkowa для книги того года, в который попадает entry_date.

    Сквозная в пределах года, с нового года — снова с 1.
    Основывается на MAX(lp), а не на количестве строк: после сторно в середине
    книги нумерация обязана продолжаться, а не переиспользовать освободившийся номер.
    """
    if not hasattr(entry_date, "year"):
        raise TypeError(f"entry_date must be a date, got {type(entry_date).__name__}")

    year_prefix = f"{entry_date.year:04d}-%"
    cursor = conn.execute(
        """
        SELECT COALESCE(MAX(lp), 0) + 1
        FROM kpir_entries
        WHERE entry_date LIKE ?
        """,
        (year_prefix,),
    )
    row = cursor.fetchone()
    return int(row[0])


def close_month(conn: sqlite3.Connection, period: str) -> PeriodTotals:
    """Агрегат по всем проводкам месяца: przychody — сумма col_9_razem_przychody,
    koszty — сумма col_14_razem_wydatki. Период задаётся строкой 'YYYY-MM'.

    Обязательное поведение:
    - месяц без проводок → ValueError (с упоминанием периода в сообщении);
    - неверный формат периода → ValueError;
    - деньги наружу выходят как Decimal (приводить через str(), не Decimal(float)).
    """
    if not isinstance(period, str) or not _PERIOD_RE.match(period):
        raise ValueError(f"Invalid period format: {period!r}, expected 'YYYY-MM'")

    cursor = conn.execute(
        """
        SELECT col_9_razem_przychody, col_14_razem_wydatki
        FROM kpir_entries
        WHERE entry_date LIKE ? OR entry_date = ?
        """,
        (f"{period}-%", period),
    )
    rows = cursor.fetchall()

    if not rows:
        raise ValueError(f"No kpir entries found for period {period}")

    przychody = sum(
        (Decimal(str(r[0] if r[0] is not None else 0.0)) for r in rows),
        Decimal("0.00"),
    ).quantize(Decimal("0.01"))

    koszty = sum(
        (Decimal(str(r[1] if r[1] is not None else 0.0)) for r in rows),
        Decimal("0.00"),
    ).quantize(Decimal("0.01"))

    dochod = przychody - koszty

    return PeriodTotals(
        period=period,
        przychody=przychody,
        koszty=koszty,
        dochod=dochod,
        entries=len(rows),
    )
