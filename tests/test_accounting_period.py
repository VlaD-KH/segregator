"""
tests/test_accounting_period.py
Контракт модуля `segregator.accounting.period` — нумерация KPiR и закрытие месяца.

Эти тесты написаны ДО реализации и намеренно красные. Они и есть задание:
реализация считается готовой, когда все они зелёные, и ни один из них не
изменён под удобство реализации.

Почему модуль отдельный. Сейчас месячные цифры берутся из одного документа:
`orchestrator/nodes.py:209` подставляет захардкоженные `Decimal('10000.00')`,
а `service.py` пишет их через `INSERT OR REPLACE`, так что последний
обработанный документ месяца затирает весь месяц. Перестановка файлов меняет
объявленный взнос — это прямо нарушает правило `CLAUDE.md`: «любая цифра в
выдаче агента должна быть прослеживаема до вызова калькулятора или до поля
документа». Агрегат обязан считаться из проводок за период, а не из последнего
документа.

`kpir_entries` не содержит `taxpayer_nip` — система однопользовательская
(`SPEC.md`: «Пользователь один — владелец JDG»), поэтому агрегат считается
по периоду целиком.
"""

from datetime import date
from decimal import Decimal
import sqlite3

import pytest

from segregator.accounting.period import PeriodTotals, close_month, next_lp
from segregator.db.migrate import migrate


@pytest.fixture
def conn(tmp_path):
    """Пустая база с применёнными миграциями."""
    db_path = tmp_path / "test.db"
    migrate(db_path)
    connection = sqlite3.connect(db_path)
    yield connection
    connection.close()


def _add_entry(
    connection: sqlite3.Connection,
    *,
    lp: int,
    entry_date: str,
    przychody: str = "0.00",
    wydatki: str = "0.00",
    doc_number: str = "FV/1",
) -> None:
    """Проводка в kpir_entries. Колонки 9 и 14 — итоговые, их и агрегируем."""
    connection.execute(
        """
        INSERT INTO kpir_entries (
            lp, entry_date, doc_number, counterparty_name, description,
            col_9_razem_przychody, col_14_razem_wydatki, created_at
        ) VALUES (?, ?, ?, 'Kontrahent', 'opis', ?, ?, '2026-01-01T00:00:00Z')
        """,
        (lp, entry_date, doc_number, float(przychody), float(wydatki)),
    )
    connection.commit()


# ---------------------------------------------------------------------------
# next_lp — liczba porządkowa
# ---------------------------------------------------------------------------
# Колонка 1 книги — liczba porządkowa по закону. Сейчас `nodes.py:181` ставит
# lp=1 каждой строке, и реестр за месяц выходит с тремя строками под номером 1.
# Нумерация сквозная в пределах ГОДА и начинается заново с нового года.

def test_next_lp_starts_at_one_on_empty_book(conn):
    assert next_lp(conn, date(2025, 11, 10)) == 1


def test_next_lp_increments_within_the_year(conn):
    _add_entry(conn, lp=1, entry_date="2025-11-10")
    assert next_lp(conn, date(2025, 11, 20)) == 2
    _add_entry(conn, lp=2, entry_date="2025-11-20")
    assert next_lp(conn, date(2025, 12, 1)) == 3


def test_next_lp_restarts_each_year(conn):
    _add_entry(conn, lp=1, entry_date="2025-11-10")
    _add_entry(conn, lp=2, entry_date="2025-12-31")
    # Новый год — новая книга, нумерация с единицы.
    assert next_lp(conn, date(2026, 1, 2)) == 1


def test_next_lp_ignores_other_years(conn):
    _add_entry(conn, lp=1, entry_date="2024-05-05")
    _add_entry(conn, lp=2, entry_date="2024-06-06")
    _add_entry(conn, lp=3, entry_date="2024-07-07")
    assert next_lp(conn, date(2025, 1, 1)) == 1


def test_next_lp_survives_gaps(conn):
    """После сторно в середине книги нумерация продолжается с максимума, не с count."""
    _add_entry(conn, lp=1, entry_date="2025-03-01")
    _add_entry(conn, lp=7, entry_date="2025-03-02")
    assert next_lp(conn, date(2025, 3, 3)) == 8


# ---------------------------------------------------------------------------
# close_month — агрегаты периода
# ---------------------------------------------------------------------------

def test_close_month_sums_entries_of_the_period(conn):
    _add_entry(conn, lp=1, entry_date="2025-11-05", przychody="12000.00")
    _add_entry(conn, lp=2, entry_date="2025-11-18", wydatki="836.25")
    _add_entry(conn, lp=3, entry_date="2025-11-30", wydatki="1000.00")

    totals = close_month(conn, "2025-11")

    assert isinstance(totals, PeriodTotals)
    assert totals.period == "2025-11"
    assert totals.entries == 3
    assert totals.przychody == Decimal("12000.00")
    assert totals.koszty == Decimal("1836.25")
    assert totals.dochod == Decimal("10163.75")


def test_close_month_returns_decimal_not_float(conn):
    """Деньги — Decimal. В БД они лежат как REAL, наружу обязаны выходить точными."""
    _add_entry(conn, lp=1, entry_date="2025-11-05", przychody="0.10")
    _add_entry(conn, lp=2, entry_date="2025-11-06", przychody="0.20")

    totals = close_month(conn, "2025-11")

    assert isinstance(totals.przychody, Decimal)
    assert isinstance(totals.koszty, Decimal)
    assert isinstance(totals.dochod, Decimal)
    # 0.1 + 0.2 в двоичной плавающей арифметике даёт 0.30000000000000004.
    assert totals.przychody == Decimal("0.30")


def test_close_month_ignores_other_periods(conn):
    _add_entry(conn, lp=1, entry_date="2025-10-31", przychody="999.00")
    _add_entry(conn, lp=2, entry_date="2025-11-01", przychody="100.00")
    _add_entry(conn, lp=3, entry_date="2025-12-01", przychody="999.00")

    totals = close_month(conn, "2025-11")

    assert totals.entries == 1
    assert totals.przychody == Decimal("100.00")


def test_close_month_refuses_empty_period(conn):
    """Месяц без проводок — отказ считать, а не нули.

    Нулевой доход и отсутствие данных — разные вещи. Первое означает, что
    предприниматель ничего не заработал; второе — что документы ещё не
    обработаны. Вернув нули, модуль отдал бы декларацию за необработанный
    месяц (`agents/04_doradca.yaml`: отказываться считать, если таблицы нет).
    """
    with pytest.raises(ValueError, match="2025-11"):
        close_month(conn, "2025-11")


def test_close_month_rejects_malformed_period(conn):
    _add_entry(conn, lp=1, entry_date="2025-11-05", przychody="100.00")
    for bad in ("2025-13", "ноябрь", "2025/11", "2025-1", ""):
        with pytest.raises(ValueError):
            close_month(conn, bad)


def test_close_month_dochod_can_be_negative(conn):
    """Убыточный месяц — законное состояние, обрезать по нулю нельзя."""
    _add_entry(conn, lp=1, entry_date="2025-11-05", przychody="1000.00")
    _add_entry(conn, lp=2, entry_date="2025-11-06", wydatki="3000.00")

    totals = close_month(conn, "2025-11")

    assert totals.dochod == Decimal("-2000.00")
