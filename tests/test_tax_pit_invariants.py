"""
tests/test_tax_pit_invariants.py
Модульные тесты для детерминированного налогового калькулятора (PIT/KPiR) и математических инвариантов.
"""

from datetime import date
from decimal import Decimal
import pytest

from src.segregator.domain.models import (
    TaxRegime,
    DocumentFacts,
    ExtractedField,
    DataSource,
    BookingProposal,
    EmploymentType,
)
from src.segregator.tax.pit import PITCalculator, PITConstants
from src.segregator.accounting.kpir import KPiREngine, KPiRColumn
from src.segregator.domain.invariants import InvariantEngine


def test_calculate_skala_tax_brackets():
    """Тест прогрессивной налоговой шкалы (Skala podatkowa 12% / 32% + Kwota wolna 30k zł)."""
    # 1. Доход 25 000 zł (в пределах kwota wolna 30k zł) -> Налог 0 zł
    assert PITCalculator.calculate_skala_tax(Decimal('25000.00')) == Decimal('0.00')
    
    # 2. Доход 30 000 zł -> Налог ровно 0 zł (30 000 * 12% - 3600 = 0)
    assert PITCalculator.calculate_skala_tax(Decimal('30000.00')) == Decimal('0.00')
    
    # 3. Доход 100 000 zł (до порога 120k) -> 100 000 * 12% - 3600 = 8 400 zł
    assert PITCalculator.calculate_skala_tax(Decimal('10000.00')) == Decimal('0.00')
    assert PITCalculator.calculate_skala_tax(Decimal('100000.00')) == Decimal('8400.00')
    
    # 4. Доход 120 000 zł (граница порога) -> 120 000 * 12% - 3600 = 10 800 zł
    assert PITCalculator.calculate_skala_tax(Decimal('120000.00')) == Decimal('10800.00')
    
    # 5. Доход 150 000 zł (превышение порога на 30k) -> 10 800 + 30 000 * 32% = 10 800 + 9 600 = 20 400 zł
    assert PITCalculator.calculate_skala_tax(Decimal('150000.00')) == Decimal('20400.00')


def test_calculate_monthly_jdg_advance_cumulative():
    """Тест кумулятивного расчета аванса PIT с переходом через порог 120k zł."""
    # Месяц 1: Доход 80 000, Расход 20 000 -> База 60 000 -> Налог 60 000 * 12% - 3600 = 3 600 zł
    res_m1 = PITCalculator.calculate_monthly_jdg_advance(
        month="2025-06",
        regime=TaxRegime.SKALA,
        income_ytd=Decimal('80000.00'),
        costs_ytd=Decimal('20000.00'),
        social_zus_paid_ytd=Decimal('0.00'),
        advances_paid_prior=Decimal('0.00')
    )
    assert res_m1.tax_base_ytd == Decimal('60000.00')
    assert res_m1.advance_to_pay == Decimal('3600.00')
    assert res_m1.threshold_exceeded is False

    # Месяц 2: Доход кумулятивно 180 000, Расход 40 000 -> База 140 000 (превышен порог 120k)
    # Налог YTD: 10 800 + 20 000 * 32% = 10 800 + 6 400 = 17 200 zł
    # Аванс к уплате = 17 200 - 3 600 = 13 600 zł
    res_m2 = PITCalculator.calculate_monthly_jdg_advance(
        month="2025-07",
        regime=TaxRegime.SKALA,
        income_ytd=Decimal('180000.00'),
        costs_ytd=Decimal('40000.00'),
        social_zus_paid_ytd=Decimal('0.00'),
        advances_paid_prior=Decimal('3600.00')
    )
    assert res_m2.tax_base_ytd == Decimal('140000.00')
    assert res_m2.tax_due_ytd == Decimal('17200.00')
    assert res_m2.advance_to_pay == Decimal('13600.00')
    assert res_m2.threshold_exceeded is True


def test_payroll_calculations():
    """Тест расчета заработной платы по UoP и UZ (включая молодежь <26)."""
    # 1. UoP: Зарплата 10 000 zł брутто
    # Соцвзносы: 10000 * (9.76% + 1.50% + 2.45%) = 1371.00 zł
    # База zdrowotna = 8629.00 zł -> Zdrowotna 9% = 776.61 zł
    # База PIT = 10000 - 1371 - 250 (KUP) = 8379.00 zł
    # Налог 12% = 1005.48 - 300 (PIT-2) = 705.48 -> 705.00 zł
    # Netto = 10000 - 1371 - 776.61 - 705 = 7147.39 zł
    uop = PITCalculator.calculate_uop_payroll(gross_salary=Decimal('10000.00'), has_pit2=True)
    assert uop.employee_social_zus == Decimal('1371.00')
    assert uop.zdrowotna == Decimal('776.61')
    assert uop.advance_pit == Decimal('705.00')
    assert uop.net_salary == Decimal('7147.39')

    # 2. UZ: Студент до 26 лет -> Brutto = Netto (0% PIT, 0% ZUS)
    uz_young = PITCalculator.calculate_uz_payroll(gross_salary=Decimal('5000.00'), is_student_under_26=True)
    assert uz_young.net_salary == Decimal('5000.00')
    assert uz_young.employee_social_zus == Decimal('0.00')
    assert uz_young.advance_pit == Decimal('0.00')


def test_kpir_engine_mixed_vehicle_booking():
    """
    Тест разнесения фактуры за топливо на легковой автомобиль (смешанное использование):
    - Фактура: Netto 1000.00 zł, VAT 230.00 zł, Brutto 1230.00 zł
    - 50% вычет VAT = 115.00 zł
    - Невычитаемый VAT = 115.00 zł
    - База расхода = 1000 + 115 = 1115.00 zł
    - В KUP (колонка 13) идет 75% от 1115.00 zł = 836.25 zł
    """
    facts = DocumentFacts(
        doc_type="faktura",
        doc_number=ExtractedField(value="FV/2026/08/100", source=DataSource.KSEF),
        doc_date=ExtractedField(value=date(2026, 8, 15), source=DataSource.KSEF),
        seller_name=ExtractedField(value="PKN ORLEN", source=DataSource.KSEF),
        netto=ExtractedField(value=Decimal('1000.00'), source=DataSource.KSEF),
        vat=ExtractedField(value=Decimal('230.00'), source=DataSource.KSEF),
        brutto=ExtractedField(value=Decimal('1230.00'), source=DataSource.KSEF)
    )

    proposal = BookingProposal(
        category="Paliwo do samochodu",
        kpir_column=13,
        vehicle_usage_type="mixed",
        kup_deductible_ratio=Decimal('0.75'),
        vat_deductible_ratio=Decimal('0.50')
    )

    entry = KPiREngine.book_document(facts=facts, proposal=proposal, is_company_vat_payer=True)

    assert entry.col_13_pozostale_wydatki == Decimal('836.25')
    assert entry.col_14_razem_wydatki == Decimal('836.25')
    assert entry.vat_amount == Decimal('115.00')


def test_invariant_engine_all_checks():
    """Тест всех 5 математических инвариантов на правильных и ошибочных данных."""
    # 1. Document Math
    inv1_ok = InvariantEngine.check_document_math(Decimal('1000.00'), Decimal('230.00'), Decimal('1230.00'))
    assert inv1_ok.passed is True
    
    inv1_fail = InvariantEngine.check_document_math(Decimal('1000.00'), Decimal('230.00'), Decimal('1300.00'))
    assert inv1_fail.passed is False
    assert inv1_fail.delta == Decimal('70.00')

    # 2. Cashflow Balance
    inv2_ok = InvariantEngine.check_cashflow_balance(
        saldo_start=Decimal('5000.00'),
        inflows=Decimal('12000.00'),
        outflows=Decimal('7000.00'),
        saldo_end=Decimal('10000.00')
    )
    assert inv2_ok.passed is True

    # 3. ZUS DRA Convergence
    inv3_ok = InvariantEngine.check_zus_dra_convergence(
        dra_total=Decimal('1500.00'),
        rca_contributions=[Decimal('1000.00'), Decimal('500.00')]
    )
    assert inv3_ok.passed is True

    inv3_fail = InvariantEngine.check_zus_dra_convergence(
        dra_total=Decimal('1600.00'),
        rca_contributions=[Decimal('1000.00'), Decimal('500.00')]
    )
    assert inv3_fail.passed is False
    assert inv3_fail.delta == Decimal('100.00')

    # 4. Double Entry
    inv6_ok = InvariantEngine.check_double_entry_balance(
        debits=[Decimal('500.00'), Decimal('700.00')],
        credits=[Decimal('1200.00')]
    )
    assert inv6_ok.passed is True
