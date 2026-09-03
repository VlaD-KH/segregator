"""
tests/test_domain_zus.py
Модульные тесты для доменных моделей и детерминированного калькулятора ZUS.
"""

from datetime import date
from decimal import Decimal
import pytest

from segregator.domain.models import (
    EmploymentPeriod,
    EmploymentTypeKind,
    TaxpayerProfile,
    TaxRegime,
    ZUSStage,
    DocumentType,
    AgentDecision,
    DocumentFacts,
    ExtractedField,
    DataSource,
    BookingProposal,
)
from segregator.domain.zus import ZUSCalculator, ZUSConstants


def test_document_facts_and_booking_proposal():
    """Тест создания и валидации контрактов DocumentFacts и BookingProposal."""
    facts = DocumentFacts(
        doc_type=DocumentType.FAKTURA_KOSZTOWA,
        fields={
            "nip_sprzedawcy": ExtractedField(value="5252344078", source=DataSource.KSEF, confidence=1.0),
            "netto": ExtractedField(value=10000.0, source=DataSource.KSEF, confidence=1.0),
            "vat": ExtractedField(value=2300.0, source=DataSource.KSEF, confidence=1.0),
            "brutto": ExtractedField(value=12300.0, source=DataSource.KSEF, confidence=1.0),
        },
        decision=AgentDecision.OK
    )
    assert facts.doc_type == DocumentType.FAKTURA_KOSZTOWA
    assert facts.seller_nip == "5252344078"
    assert facts.netto == Decimal('10000.00')

    proposal = BookingProposal(
        category="Koszty operacyjne",
        kpir_column=13,
        pit_cost_ratio=0.75,
        vat_deduction_ratio=0.50,
        confidence=1.0,
        basis="rule:test",
        decision=AgentDecision.OK
    )
    assert proposal.kpir_column == 13
    assert proposal.pit_cost_ratio == 0.75


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
                emp_type=EmploymentTypeKind.UOP,
                start_date=date(2025, 1, 1),
                end_date=date(2025, 5, 31),
                monthly_gross_avg=Decimal('8000.00')
            ),
            EmploymentPeriod(
                emp_type=EmploymentTypeKind.JDG,
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
    - Składka Zdrowotna = 9% от базы (база не ниже минимальной ЗП)
    - Формы: только ZUS DRA — ноябрь не месяц смены титула страхования
    """
    profile = TaxpayerProfile(
        pesel_masked="900101*****",
        nip="5252344078",
        date_of_birth=date(1990, 1, 1),
        jdg_tax_regime=TaxRegime.SKALA,
        employment_history=[
            EmploymentPeriod(
                emp_type=EmploymentTypeKind.JDG,
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
    # Ноябрь — не месяц смены титула (октябрь тоже Ulga na start), поэтому
    # регистрационных форм нет: только месячная декларация.
    assert obligations.forms_required == ["ZUS DRA"]


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
                emp_type=EmploymentTypeKind.JDG,
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
    # Май 2025 — не месяц перехода (апрель тоже Preferencyjny): только DRA.
    assert obligations.forms_required == ["ZUS DRA"]


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
                emp_type=EmploymentTypeKind.UOP,
                start_date=date(2025, 1, 1),
                end_date=None,
                monthly_gross_avg=Decimal('7000.00')
            ),
            EmploymentPeriod(
                emp_type=EmploymentTypeKind.JDG,
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
    assert obligations.forms_required == ["ZUS DRA"]


# ---------------------------------------------------------------------------
# Формы отчётности: DRA ежемесячно, регистрационные — только в месяц смены титула
# ---------------------------------------------------------------------------

def _jdg_profile(start: date, regime: TaxRegime = TaxRegime.SKALA) -> TaxpayerProfile:
    return TaxpayerProfile(
        pesel_masked="900101*****",
        nip="5252344078",
        date_of_birth=date(1990, 1, 1),
        jdg_tax_regime=regime,
        employment_history=[
            EmploymentPeriod(emp_type=EmploymentTypeKind.JDG, start_date=start)
        ],
    )


def test_registration_forms_only_in_month_of_stage_change():
    """ZWUA+ZUA подаются в месяц перехода Ulga na start -> Preferencyjny, и только в него.

    ZUA — регистрационная форма (постановка на полное страхование), а не месячный
    отчёт. Пока она приклеивалась к каждому месяцу, выдача предлагала подавать её
    ежемесячно.
    """
    profile = _jdg_profile(date(2025, 1, 1))

    # Июнь: 5-й месяц -> ещё Ulga na start, титул не менялся.
    june = ZUSCalculator.calculate_monthly_obligations(
        profile=profile, target_month=date(2025, 6, 1), jdg_monthly_profit=Decimal('10000.00')
    )
    assert june.stage == ZUSStage.ULGA_NA_START
    assert june.forms_required == ["ZUS DRA"]

    # Июль: 6-й месяц -> переход на Preferencyjny. Снятие старого титула + постановка.
    july = ZUSCalculator.calculate_monthly_obligations(
        profile=profile, target_month=date(2025, 7, 1), jdg_monthly_profit=Decimal('10000.00')
    )
    assert july.stage == ZUSStage.PREFERENCYJNY
    assert july.forms_required == ["ZUS DRA", "ZUS ZWUA", "ZUS ZUA"]

    # Август: ступень та же -> снова только месячная декларация.
    august = ZUSCalculator.calculate_monthly_obligations(
        profile=profile, target_month=date(2025, 8, 1), jdg_monthly_profit=Decimal('10000.00')
    )
    assert august.forms_required == ["ZUS DRA"]


def test_first_month_registers_without_zwua():
    """В месяц открытия JDG снимать нечего — ZWUA не подаётся."""
    obligations = ZUSCalculator.calculate_monthly_obligations(
        profile=_jdg_profile(date(2025, 10, 1)),
        target_month=date(2025, 10, 1),
        jdg_monthly_profit=Decimal('12000.00'),
    )
    assert obligations.forms_required == ["ZUS DRA", "ZUS ZZA"]


def test_no_obligations_before_jdg_start():
    """Месяц до открытия JDG: обязательств нет, включая składkę zdrowotną.

    Раньше ветвление по ступеням не совпадало с BRAK, расчёт проваливался дальше
    и начислял медицинский взнос за месяц без деятельности.
    """
    obligations = ZUSCalculator.calculate_monthly_obligations(
        profile=_jdg_profile(date(2025, 10, 1)),
        target_month=date(2025, 9, 1),
        jdg_monthly_profit=Decimal('12000.00'),
    )
    assert obligations.stage == ZUSStage.BRAK
    assert obligations.skladka_zdrowotna == Decimal('0.00')
    assert obligations.total_spoleczne == Decimal('0.00')
    assert obligations.total_zus_do_zaplaty == Decimal('0.00')
    assert obligations.forms_required == []


# ---------------------------------------------------------------------------
# Składka zdrowotna: пол применяется к базе, а не к готовому взносу
# ---------------------------------------------------------------------------

def test_zdrowotna_liniowy_floors_base_not_contribution():
    """Для liniowego минимум — это минималка как БАЗА, дальше своя ставка 4.9%.

    Прежний пол `min_wage * 9%` применялся ко всем режимам: liniowy получал
    419.94 вместо 228.63 за 2025 — почти вдвое больше должного.
    """
    obligations = ZUSCalculator.calculate_monthly_obligations(
        profile=_jdg_profile(date(2020, 1, 1), regime=TaxRegime.LINIOWY),
        target_month=date(2025, 5, 1),
        jdg_monthly_profit=Decimal('3000.00'),  # ниже минималки 4666.00
    )
    assert obligations.zdrowotna_base == Decimal('4666.00')
    assert obligations.skladka_zdrowotna == Decimal('228.63')  # 4666.00 * 4.9%


def test_zdrowotna_skala_above_floor_uses_profit():
    """Выше пола база — реальная прибыль (для skali пол и ставка совпадают в 9%)."""
    obligations = ZUSCalculator.calculate_monthly_obligations(
        profile=_jdg_profile(date(2020, 1, 1), regime=TaxRegime.SKALA),
        target_month=date(2025, 5, 1),
        jdg_monthly_profit=Decimal('10000.00'),
    )
    assert obligations.zdrowotna_base == Decimal('10000.00')
    assert obligations.skladka_zdrowotna == Decimal('900.00')


# ---------------------------------------------------------------------------
# Ryczałt: три ступени базы по годовому przychodowi
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "annual_revenue, expected_base, expected_zdrowotna",
    [
        (Decimal('50000.00'), Decimal('5129.51'), Decimal('461.66')),    # <= 60k  -> 60%
        (Decimal('200000.00'), Decimal('8549.18'), Decimal('769.43')),   # <= 300k -> 100%
        (Decimal('400000.00'), Decimal('15388.52'), Decimal('1384.97')), # > 300k  -> 180%
    ],
)
def test_ryczalt_zdrowotna_three_tiers(annual_revenue, expected_base, expected_zdrowotna):
    """База ryczałtu — 60/100/180% od przeciętnego w sektorze przedsiębiorstw IV кв.

    Показатель отличается от prognozowanego przeciętnego wynagrodzenia, на котором
    строится база dużego ZUS: за 2025 это 8549.18, а не 8673.00.
    """
    obligations = ZUSCalculator.calculate_monthly_obligations(
        profile=_jdg_profile(date(2020, 1, 1), regime=TaxRegime.RYCZALT),
        target_month=date(2025, 5, 1),
        annual_revenue=annual_revenue,
    )
    assert obligations.zdrowotna_base == expected_base
    assert obligations.skladka_zdrowotna == expected_zdrowotna


def test_ryczalt_without_annual_revenue_refuses():
    """Без годового przychodu ступень неизвестна — считать отказываемся, а не гадаем."""
    with pytest.raises(ValueError, match="annual_revenue"):
        ZUSCalculator.calculate_monthly_obligations(
            profile=_jdg_profile(date(2020, 1, 1), regime=TaxRegime.RYCZALT),
            target_month=date(2025, 5, 1),
        )


def test_ryczalt_unknown_year_refuses():
    """Года без таблицы przeciętnego wynagrodzenia -> Odmowa kalkulacji."""
    with pytest.raises(ValueError, match="Odmowa kalkulacji"):
        ZUSConstants.get_ryczalt_zdrowotna_base(2030, Decimal('100000.00'))
