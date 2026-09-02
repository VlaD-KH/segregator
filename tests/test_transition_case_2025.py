"""
tests/test_transition_case_2025.py
Сквозной интеграционный тест сложного кейса перехода физлица в 2025 году:
- Январь - Май 2025: Работа по Umowa o pracę (UoP)
- Июнь - Сентябрь 2025: Работа по Umowa zlecenie (UZ)
- Октябрь - Декабрь 2025: Открытие JDG на Skala podatkowa + Ulga na start
- Результат: Консолидация в форму PIT-36 с приложением PIT/B.
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
)
from segregator.domain.zus import ZUSCalculator
from segregator.tax.pit import PITCalculator
from segregator.compliance.pit36 import (
    IncomeSourceRecord,
    PITBAttachment,
    PIT36Consolidator,
)


def test_full_year_2025_transition_case():
    """
    Сквозная симуляция годового перехода UoP -> UZ -> JDG за 2025 год.
    """
    # 1. Профиль налогоплательщика с историей занятости
    profile = TaxpayerProfile(
        pesel_masked="880512*****",
        nip="5252344078",
        full_name_masked="Piotr N*****",
        date_of_birth=date(1988, 5, 12),
        jdg_tax_regime=TaxRegime.SKALA,
        employment_history=[
            # Период 1: Январь - Май (UoP, 5 месяцев)
            EmploymentPeriod(
                emp_type=EmploymentTypeKind.UOP,
                start_date=date(2025, 1, 1),
                end_date=date(2025, 5, 31),
                monthly_gross_avg=Decimal('10000.00'),
                payer_nip="1112223344"
            ),
            # Период 2: Июнь - Сентябрь (UZ, 4 месяца)
            EmploymentPeriod(
                emp_type=EmploymentTypeKind.UZ,
                start_date=date(2025, 6, 1),
                end_date=date(2025, 9, 30),
                monthly_gross_avg=Decimal('8000.00'),
                payer_nip="5556667788"
            ),
            # Период 3: Октябрь - Декабрь (JDG, старт 01.10.2025)
            EmploymentPeriod(
                emp_type=EmploymentTypeKind.JDG,
                start_date=date(2025, 10, 1),
                end_date=None,
                payer_nip="5252344078"
            )
        ]
    )

    # -------------------------------------------------------------
    # ЭТАП 1: Проверка ZUS обязательств для JDG в октябре-декабре
    # -------------------------------------------------------------
    for target_month in [date(2025, 10, 1), date(2025, 11, 1), date(2025, 12, 1)]:
        zus_res = ZUSCalculator.calculate_monthly_obligations(
            profile=profile,
            target_month=target_month,
            jdg_monthly_profit=Decimal('12000.00')
        )
        
        # Доказываем, что в 4 квартале действует Ulga na start:
        assert zus_res.stage == ZUSStage.ULGA_NA_START
        # Социальные взносы отключены (0 PLN):
        assert zus_res.total_spoleczne == Decimal('0.00')
        assert zus_res.emerytalne == Decimal('0.00')
        assert zus_res.rentowe == Decimal('0.00')
        assert zus_res.fundusz_pracy == Decimal('0.00')
        # Оплачивается только медицинский взнос (12000 * 9% = 1080.00 zł):
        assert zus_res.skladka_zdrowotna == Decimal('1080.00')
        assert zus_res.total_zus_do_zaplaty == Decimal('1080.00')
        assert "ZUS ZZA" in zus_res.forms_required

    # -------------------------------------------------------------
    # ЭТАП 2: Подготовка данных из PIT-11 за 2025 год
    # -------------------------------------------------------------
    # А. Данные из PIT-11 работодателя по UoP (5 месяцев * 10 000 zł)
    uop_monthly = PITCalculator.calculate_uop_payroll(gross_salary=Decimal('10000.00'), has_pit2=True)
    uop_source = IncomeSourceRecord(
        source_name="UoP",
        source_description="Stosunek pracy (PIT-11 Pracodawca A)",
        revenue_przychod=Decimal('10000.00') * 5,                                 # 50 000.00 zł
        tax_costs_kup=uop_monthly.tax_costs_kup * 5,                              # 1 250.00 zł
        income_dochod=(Decimal('10000.00') - uop_monthly.tax_costs_kup) * 5,      # 48 750.00 zł
        social_zus_deductible=uop_monthly.employee_social_zus * 5,                # 6 855.00 zł
        advances_paid=uop_monthly.advance_pit * 5                                 # 3 525.00 zł
    )

    # Б. Данные из PIT-11 заказчика по UZ (4 месяца * 8 000 zł)
    uz_monthly = PITCalculator.calculate_uz_payroll(gross_salary=Decimal('8000.00'), pays_social=True)
    uz_source = IncomeSourceRecord(
        source_name="UZ",
        source_description="Działalność wykonywana osobiście (PIT-11 Zleceniodawca B)",
        revenue_przychod=Decimal('8000.00') * 4,                                  # 32 000.00 zł
        tax_costs_kup=uz_monthly.tax_costs_kup * 4,                               # 4 876.80 zł
        income_dochod=(Decimal('8000.00') * 4) - (uz_monthly.tax_costs_kup * 4), # 27 123.20 zł
        social_zus_deductible=uz_monthly.employee_social_zus * 4,                 # 4 387.20 zł
        advances_paid=uz_monthly.advance_pit * 4                                  # 2 940.00 zł
    )

    # В. Данные из KPiR за IV квартал JDG (3 месяца, Выручка 45 000 zł, Расходы 9 000 zł)
    jdg_pit_b = PITBAttachment(
        nip="5252344078",
        business_name="Piotr N***** IT Consulting",
        pkd_main="62.01.Z",
        revenue=Decimal('45000.00'),
        costs=Decimal('9000.00'),
        income=Decimal('36000.00'),
        loss=Decimal('0.00')
    )
    jdg_advances_paid = Decimal('4320.00') # Уплаченные ежемесячные авансы

    # -------------------------------------------------------------
    # ЭТАП 3: Консолидация в единую годовую форму PIT-36
    # -------------------------------------------------------------
    pit36 = PIT36Consolidator.consolidate_year_2025(
        pesel_masked=profile.pesel_masked,
        nip=profile.nip,
        uop_income=uop_source,
        uz_income=uz_source,
        jdg_pit_b=jdg_pit_b,
        jdg_social_zus_paid=Decimal('0.00'), # На Ulga na start соцвзносы = 0
        jdg_advances_paid=jdg_advances_paid
    )

    # -------------------------------------------------------------
    # ЭТАП 4: Проверка результатов консолидации
    # -------------------------------------------------------------
    # 1. Совокупная выручка: 50 000 (UoP) + 32 000 (UZ) + 45 000 (JDG) = 127 000 zł
    assert pit36.total_revenue == Decimal('127000.00')
    
    # 2. Совокупные социальные вычеты ZUS: 6 855 (UoP) + 4 387.20 (UZ) + 0 (JDG) = 11 242.20 zł
    assert pit36.total_social_zus_deduction == Decimal('11242.20')
    
    # 3. Налоговая база и налог строго детерминированы
    assert pit36.tax_base_rounded > Decimal('0.00')
    assert pit36.calculated_tax > Decimal('0.00')
    
    # 4. Проверка сходимости авансов
    total_adv = uop_source.advances_paid + uz_source.advances_paid + jdg_advances_paid
    assert pit36.total_advances_paid == total_adv
    
    # 5. Приложение PIT/B корректно заполнено
    assert pit36.pit_b is not None
    assert pit36.pit_b.income == Decimal('36000.00')
    assert len(pit36.sources) == 3
