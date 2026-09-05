"""Закрытие месяца: агрегаты из kpir_entries вместо заглушки, история вместо перезаписи.

Блокер 4 (serene-honking-flurry.md, 2.3) и незакрытый хвост миграции 0003.

Два разных дефекта, которые лечатся одним заходом:

1. `nodes.py:209` подставляет `monthly_profit = Decimal('10000.00')` и
   перезаписывает её только если у документа есть выручка. `nodes.py` не имеет
   доступа к БД, поэтому настоящий агрегат месяца может посчитать только
   сервис — отдельным шагом закрытия периода, после проводки всех документов.
2. `INSERT OR REPLACE` в `_save_results_to_db` писался под `UNIQUE
   (taxpayer_nip, period_month)`. Миграция 0003 это ограничение сняла и
   заменила частичным индексом по `superseded_at IS NULL` — теперь
   `OR REPLACE` не заменяет строку, а **удаляет** прошлую и вставляет новую.
   Инвариант 5 DATA_BOUNDARY.md требует append-only.
"""

from datetime import date
from decimal import Decimal
import sqlite3

import pytest

from segregator.domain.models import (
    AgentDecision,
    DataSource,
    DocumentFacts,
    DocumentType,
    EmploymentPeriod,
    EmploymentTypeKind,
    ExtractedField,
    TaxRegime,
    TaxpayerProfile,
)
from segregator.service import SegregatorService


def _profile(regime: TaxRegime = TaxRegime.SKALA) -> TaxpayerProfile:
    return TaxpayerProfile(
        pesel_masked="900101*****",
        nip="5252344078",
        date_of_birth=date(1990, 1, 1),
        jdg_tax_regime=regime,
        employment_history=[
            EmploymentPeriod(emp_type=EmploymentTypeKind.JDG, start_date=date(2025, 10, 1))
        ],
    )


def _sales_facts(doc_number: str, doc_date: str, netto: float):
    """Фактура продажи — колонка 7. Продавец не топливный, иначе сработает
    правило авторасходов и документ уйдёт в колонку 13."""
    vat = round(netto * 0.23, 2)
    return DocumentFacts(
        doc_type=DocumentType.FAKTURA_SPRZEDAZY,
        fields={
            "nip_sprzedawcy": ExtractedField(value="5252344078", source=DataSource.OCR, confidence=1.0),
            "nazwa_sprzedawcy": ExtractedField(value="ACME Sp. z o.o.", source=DataSource.OCR, confidence=1.0),
            "nr_dokumentu": ExtractedField(value=doc_number, source=DataSource.OCR, confidence=1.0),
            "data_wystawienia": ExtractedField(value=doc_date, source=DataSource.OCR, confidence=1.0),
            "netto": ExtractedField(value=netto, source=DataSource.OCR, confidence=1.0),
            "vat": ExtractedField(value=vat, source=DataSource.OCR, confidence=1.0),
            "brutto": ExtractedField(value=round(netto + vat, 2), source=DataSource.OCR, confidence=1.0),
        },
        decision=AgentDecision.OK,
    )


def _book(service, profile, tmp_path, name: str, doc_number: str, doc_date: str, netto: float):
    doc = tmp_path / name
    doc.write_text(f"faktura {doc_number}", encoding="utf-8")
    state = service.process_document(doc, profile, custom_facts=_sales_facts(doc_number, doc_date, netto))
    assert state.status == "completed", f"документ {doc_number} должен был провестись"
    return state


def _conn(service):
    return sqlite3.connect(service.db_path)


# --- история вместо перезаписи (хвост миграции 0003) ----------------------------


def test_second_document_supersedes_zus_row_instead_of_deleting_it(tmp_path):
    """Два документа за один месяц: прошлый расчёт закрывается, а не исчезает."""
    service = SegregatorService(workspace_root=tmp_path)
    profile = _profile()

    _book(service, profile, tmp_path, "f1.txt", "FV/1", "2025-11-05", 1000.0)
    _book(service, profile, tmp_path, "f2.txt", "FV/2", "2025-11-20", 2000.0)

    conn = _conn(service)
    try:
        total = conn.execute("SELECT COUNT(*) FROM zus_declarations").fetchone()[0]
        current = conn.execute(
            "SELECT COUNT(*) FROM zus_declarations WHERE superseded_at IS NULL"
        ).fetchone()[0]
    finally:
        conn.close()

    assert total == 2, "прошлый расчёт обязан остаться в истории (инвариант 5)"
    assert current == 1, "действующий расчёт за период ровно один"


def test_second_document_supersedes_tax_advance_row(tmp_path):
    service = SegregatorService(workspace_root=tmp_path)
    profile = _profile()

    _book(service, profile, tmp_path, "f1.txt", "FV/1", "2025-11-05", 1000.0)
    _book(service, profile, tmp_path, "f2.txt", "FV/2", "2025-11-20", 2000.0)

    conn = _conn(service)
    try:
        total = conn.execute("SELECT COUNT(*) FROM tax_advances").fetchone()[0]
        current = conn.execute(
            "SELECT COUNT(*) FROM tax_advances WHERE superseded_at IS NULL"
        ).fetchone()[0]
    finally:
        conn.close()

    assert total == 2
    assert current == 1


# --- close_period: агрегат из проводок, а не из одного документа ----------------


def test_close_period_refuses_month_without_entries(tmp_path):
    """«Без данных — отказ считать, не заглушка». Нулевой доход и отсутствие
    документов — разные вещи; вторая молча отдала бы декларацию за
    необработанный месяц."""
    service = SegregatorService(workspace_root=tmp_path)

    with pytest.raises(ValueError):
        service.close_period(_profile(), "2025-11")


def test_close_period_totals_match_the_ledger(tmp_path):
    service = SegregatorService(workspace_root=tmp_path)
    profile = _profile()

    _book(service, profile, tmp_path, "f1.txt", "FV/1", "2025-11-05", 1000.0)
    _book(service, profile, tmp_path, "f2.txt", "FV/2", "2025-11-20", 2000.0)

    closing = service.close_period(profile, "2025-11")

    conn = _conn(service)
    try:
        przychody, koszty, count = conn.execute(
            """
            SELECT COALESCE(SUM(col_9_razem_przychody), 0),
                   COALESCE(SUM(col_14_razem_wydatki), 0),
                   COUNT(*)
            FROM kpir_entries WHERE entry_date LIKE '2025-11-%'
            """
        ).fetchone()
    finally:
        conn.close()

    assert closing.totals.entries == count == 2
    assert closing.totals.przychody == Decimal(str(przychody))
    assert closing.totals.koszty == Decimal(str(koszty))
    assert closing.totals.przychody > Decimal("0.00"), "тест пуст, если выручки нет"


def test_close_period_result_is_not_the_ten_thousand_stub(tmp_path):
    """nodes.py при отсутствии выручки подставлял 10 000 zł. Закрытие месяца
    обязано считать от проводок."""
    service = SegregatorService(workspace_root=tmp_path)
    profile = _profile()

    _book(service, profile, tmp_path, "f1.txt", "FV/1", "2025-11-05", 40000.0)

    closing = service.close_period(profile, "2025-11")

    assert closing.totals.przychody == Decimal("40000.00")
    assert closing.zus.zdrowotna_base != Decimal("10000.00")
    assert closing.tax.income_ytd == closing.totals.przychody


def test_close_period_supersedes_the_per_document_estimate(tmp_path):
    """Оценка по одному документу закрывается официальным расчётом периода."""
    service = SegregatorService(workspace_root=tmp_path)
    profile = _profile()

    _book(service, profile, tmp_path, "f1.txt", "FV/1", "2025-11-05", 40000.0)
    service.close_period(profile, "2025-11")

    conn = _conn(service)
    try:
        rows = conn.execute(
            "SELECT superseded_at FROM zus_declarations WHERE period_month = '2025-11' ORDER BY id"
        ).fetchall()
    finally:
        conn.close()

    assert len(rows) == 2, "оценка и закрытие — две строки, а не одна затёртая"
    assert rows[0][0] is not None, "оценка обязана быть закрыта"
    assert rows[1][0] is None, "действующим остаётся расчёт закрытия"


# --- то, что раньше не передавалось вовсе ---------------------------------------


def test_close_period_carries_advances_paid_prior_between_months(tmp_path):
    """`advances_paid_prior` не передавался никогда — аванс считался с нуля
    каждый месяц, то есть уплачивался повторно."""
    service = SegregatorService(workspace_root=tmp_path)
    profile = _profile()

    _book(service, profile, tmp_path, "f1.txt", "FV/1", "2025-11-05", 40000.0)
    november = service.close_period(profile, "2025-11")

    _book(service, profile, tmp_path, "f2.txt", "FV/2", "2025-12-05", 40000.0)
    december = service.close_period(profile, "2025-12")

    assert november.tax.tax_due_ytd > Decimal("0.00"), "тест пуст, если налог нулевой"
    assert december.tax.advances_paid_prior == november.tax.tax_due_ytd


def test_close_period_income_accumulates_year_to_date(tmp_path):
    """income_ytd — кумулятивно с начала года: от него зависит порог 120 000 zł."""
    service = SegregatorService(workspace_root=tmp_path)
    profile = _profile()

    _book(service, profile, tmp_path, "f1.txt", "FV/1", "2025-11-05", 40000.0)
    service.close_period(profile, "2025-11")

    _book(service, profile, tmp_path, "f2.txt", "FV/2", "2025-12-05", 40000.0)
    december = service.close_period(profile, "2025-12")

    assert december.totals.przychody == Decimal("40000.00"), "агрегат месяца — только декабрь"
    assert december.tax.income_ytd == Decimal("80000.00"), "а income_ytd — с начала года"


def test_per_document_estimate_reflects_the_document_not_a_constant(tmp_path):
    """Оценка agent03 до закрытия месяца тоже не должна быть выдуманной.

    `nodes.py:209` подставлял 10 000 zł, если у документа нет выручки в
    колонке 7, и эта цифра доходила до zus_declarations.
    """
    service = SegregatorService(workspace_root=tmp_path)
    profile = _profile()

    _book(service, profile, tmp_path, "f1.txt", "FV/1", "2025-11-05", 40000.0)

    conn = _conn(service)
    try:
        (zdrowotna_base,) = conn.execute(
            "SELECT zdrowotna_base FROM zus_declarations WHERE superseded_at IS NULL"
        ).fetchone()
    finally:
        conn.close()

    assert Decimal(str(zdrowotna_base)) == Decimal("40000.00"), (
        "база zdrowotnej обязана считаться от дохода документа, а не от заглушки"
    )


def test_close_period_passes_annual_revenue_for_ryczalt(tmp_path):
    """`annual_revenue` не передавался никогда, а на ryczałcie без него
    ZUSCalculator отказывается считать: от него зависит ступень базы 60/100/180%."""
    service = SegregatorService(workspace_root=tmp_path)
    profile = _profile(TaxRegime.RYCZALT)

    _book(service, profile, tmp_path, "f1.txt", "FV/1", "2025-11-05", 40000.0)

    closing = service.close_period(profile, "2025-11")

    assert closing.zus.zdrowotna_base > Decimal("0.00")
