"""
tests/test_domain_zus.py
Модульные тесты для доменных моделей и детерминированного калькулятора ZUS.
"""

from datetime import date
from decimal import Decimal
import pytest

from src.segregator.domain.models import (
    EmploymentPeriod,
    EmploymentType,
    TaxpayerProfile,
    TaxRegime,
    ZUSStage,
    DocumentFacts,
    ExtractedField,
    DataSource,
    BookingProposal,
)
from src.segregator.domain.zus import ZUSCalculator, ZUSConstants


def test_document_facts_and_booking_proposal():
    """Тест создания и валидации контрактов DocumentFacts и BookingProposal."""
    facts = DocumentFacts(
        doc_type="faktura",
        ksef_reference_number="5252344078-20260831-0102030405-AB",
        seller_nip=ExtractedField(value="5252344078", source=DataSource.KSEF, confidence=1.0),
        netto=ExtractedField(value=Decimal('10000.00'), source=DataSource.KSEF, confidence=1.0),
        vat=ExtractedField(value=Decimal('2300.00'), source=DataSource.KSEF, confidence=1.0),
        brutto=ExtractedField(value=Decimal('12300.00'), source=DataSource.KSEF, confidence=1.0),
        decision="ok"
    )
    assert facts.doc_type == "faktura"
    assert facts.seller_nip.value == "5252344078"
    assert facts.netto.value == Decimal('10000.00')

    proposal = BookingProposal(
        category="Koszty operacyjne",
        kpir_column=13,
        vehicle_usage_type="mixed",
        kup_deductible_ratio=Decimal('0.75'),
        vat_deductible_ratio=Decimal('0.50'),
        confidence=1.0
    )
    assert proposal.kpir_column == 13
    assert proposal.kup_deductible_ratio == Decimal('0.75')


def test_determine_zus_stage_starting_first_day():
    """
    Тест хронологии стадий ZUS при открытии JDG 1-го числа месяца:
    - Месяцы 1-6 (с 01.01 по 30.06): Ulga na start
    - Месяцы 7-30 (с 01.07 по +24 мес): Preferencyjny ZUS
    - Свыше 30 месяцев: Duży ZUS
    """
    start_date = date(2025, 1, 1)
    
    # Месяц 1 (Январь 2025)
    assert ZUSCalculator.determine_zus_stage(start_date, date(2025, 1, 15)) == ZUSStage.ULGA_NA_START
    # Месяц 6 (Июнь 2025)
    assert ZUSCalculator.determine_zus_stage(start_date, date(2025, 6, 30)) == ZUSStage.ULGA_NA_START
    # Месяц 7 (Июль 2025) -> Переход на Preferencyjny
    assert ZUSCalculator.determine_zus_stage(start_date, date(2025, 7, 1)) == ZUSStage.PREFERENCYJNY
    # Месяц 30 (Июнь 2027) -> Последний месяц Preferencyjny
    assert ZUSCalculator.determine_zus_stage(start_date, date(2027, 6, 15)) == ZUSStage.PREFERENCYJNY
    # Месяц 31 (Июль 2027) -> Duży ZUS
    assert ZUSCalculator.determine_zus_stage(start_date, date(2027, 7, 1)) == ZUSStage.DUZY_ZUS


def test_determine_zus_stage_starting_mid_month():
    """
    Тест правила "неполного первого месяца" (бонусный месяц):
    Если JDG открыто 15.10.2025:
    - Октябрь 2025 (бонусный) + Ноябрь 2025 .. Апрель 2026 (6 полных месяцев) -> Ulga na start
    - Май 2026 -> Preferencyjny ZUS
    """
    start_date = date(2025, 10, 15)
    
    # Октябрь 2025 (неполный месяц)
    assert ZUSCalculator.determine_zus_stage(start_date, date(2025, 10, 20)) == ZUSStage.ULGA_NA_START
    # Апрель 2026 (6-й полный месяц)
    assert ZUSCalculator.determine_zus_stage(start_date, date(2026, 4, 30)) == ZUSStage.ULGA_NA_START
    # Май 2026 -> Переход на Preferencyjny
    assert ZUSCalculator.determine_zus_stage(start_date, date(2026, 5, 1)) == ZUSStage.PREFERENCYJNY


def test_zbieg_tytulow_with_uop():
    """
    Тест пересечения титулов (Zbieg tytułów ubezpieczeń):
    - Если есть активный UoP с ЗП >= минимальной (4666 zł в 2025) -> True
    - Если UoP завершился или ЗП < минимальной -> False
    """
    profile = TaxpayerProfile(
        pesel_masked="900101*****",
        nip="5252344078",
        date_of_birth=date(1990, 1, 1),
        employment_history=[
            EmploymentPeriod(
                emp_type=EmploymentType.UOP,
                start_date=date(2025, 1, 1),
                end_date=date(2025, 5, 31),
                monthly_gross_avg=Decimal('8000.00')
            ),
            EmploymentPeriod(
                emp_type=EmploymentType.JDG,
                start_date=date(2025, 3, 1),
                end_date=None
            )
        ]
    )
    
    # В апреле 2025 действует UoP с ЗП 8000 >= 4666 -> Zbieg активен
    assert ZUSCalculator.check_zbieg_tytulow(profile, date(2025, 4, 15)) is True
    
    # В июне 2025 UoP уже завершился -> Zbieg не активен
    assert ZUSCalculator.check_zbieg_tytulow(profile, date(2025, 6, 1)) is False


def test_calculate_monthly_obligations_ulga_na_start():
    """
    Тест расчета взносов на стадии Ulga na start:
    - Социальные взносы = 0 PLN
    - Fundusz Pracy = 0 PLN
    - Składka Zdrowotna = 9% от дохода (но не менее 9% от минимальной ЗП)
    - Формы: ZUS DRA + ZUS ZZA
    """
    profile = TaxpayerProfile(
        pesel_masked="900101*****",
        nip="5252344078",
        date_of_birth=date(1990, 1, 1),
        jdg_tax_regime=TaxRegime.SKALA,
        employment_history=[
            EmploymentPeriod(
                emp_type=EmploymentType.JDG,
                start_date=date(2025, 10, 1)
            )
        ]
    )
    
    # Расчет за ноябрь 2025 при доходе 10 000 zł
    obligations = ZUSCalculator.calculate_monthly_obligations(
        profile=profile,
        target_month=date(2025, 11, 1),
        jdg_monthly_profit=Decimal('10000.00')
    )
    
    assert obligations.stage == ZUSStage.ULGA_NA_START
    assert obligations.total_spoleczne == Decimal('0.00')
    assert obligations.fundusz_pracy == Decimal('0.00')
    # Zdrowotna: 10 000 * 9% = 900.00 PLN
    assert obligations.skladka_zdrowotna == Decimal('900.00')
    assert obligations.total_zus_do_zaplaty == Decimal('900.00')
    assert "ZUS ZZA" in obligations.forms_required


def test_calculate_monthly_obligations_preferencyjny():
    """
    Тест расчета взносов на стадии Preferencyjny ZUS (2025 год, минималка 4666 zł):
    - База = 30% * 4666 = 1399.80 zł
    - Emerytalne (19.52%) = 273.24 zł
    - Rentowe (8.00%) = 111.98 zł
    - Chorobowe (2.45%) = 34.30 zł
    - Wypadkowe (1.67%) = 23.38 zł
    - Fundusz Pracy = 0 zł (освобожден)
    - Total społeczne = 442.90 zł
    """
    profile = TaxpayerProfile(
        pesel_masked="900101*****",
        nip="5252344078",
        date_of_birth=date(1990, 1, 1),
        jdg_tax_regime=TaxRegime.SKALA,
        employment_history=[
            EmploymentPeriod(
                emp_type=EmploymentType.JDG,
                start_date=date(2024, 1, 1) # Прошло >6 месяцев, действует Preferencyjny
            )
        ]
    )
    
    obligations = ZUSCalculator.calculate_monthly_obligations(
        profile=profile,
        target_month=date(2025, 5, 1),
        jdg_monthly_profit=Decimal('10000.00'),
        include_chorobowe=True
    )
    
    assert obligations.stage == ZUSStage.PREFERENCYJNY
    assert obligations.spoleczne_base == Decimal('1399.80')
    assert obligations.emerytalne == Decimal('273.24')
    assert obligations.rentowe == Decimal('111.98')
    assert obligations.chorobowe == Decimal('34.30')
    assert obligations.wypadkowe == Decimal('23.38')
    assert obligations.fundusz_pracy == Decimal('0.00')
    assert obligations.total_spoleczne == Decimal('442.90')
    assert "ZUS RCA" in obligations.forms_required


def test_calculate_monthly_obligations_zbieg_tytulow():
    """
    Тест расчета взносов при активном Zbieg tytułów (UoP + JDG):
    Даже если по срокам действует Duży ZUS, соц. взносы = 0, платится только Zdrowotna.
    """
    profile = TaxpayerProfile(
        pesel_masked="900101*****",
        nip="5252344078",
        date_of_birth=date(1990, 1, 1),
        jdg_tax_regime=TaxRegime.SKALA,
        employment_history=[
            EmploymentPeriod(
                emp_type=EmploymentType.UOP,
                start_date=date(2025, 1, 1),
                end_date=None,
                monthly_gross_avg=Decimal('7000.00')
            ),
            EmploymentPeriod(
                emp_type=EmploymentType.JDG,
                start_date=date(2020, 1, 1) # По хронологии Duży ZUS
            )
        ]
    )
    
    obligations = ZUSCalculator.calculate_monthly_obligations(
        profile=profile,
        target_month=date(2025, 5, 1),
        jdg_monthly_profit=Decimal('5000.00')
    )
    
    assert obligations.zbieg_tytulow is True
    assert obligations.total_spoleczne == Decimal('0.00')
    # Zdrowotna: 5000 * 9% = 450.00 (но минимальная 4666 * 9% = 419.94) -> 450.00
    assert obligations.skladka_zdrowotna == Decimal('450.00')
    assert obligations.total_zus_do_zaplaty == Decimal('450.00')
    assert "ZUS ZZA" in obligations.forms_required
