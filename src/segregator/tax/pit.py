"""
src/segregator/tax/pit.py
Детерминированный калькулятор подоходного налога (PIT: Skala, Liniowy, Ryczałt) и заработной платы (UoP, UZ).
Строгая арифметика на базе Decimal с поддержкой кумулятивного годового учета и порогов.
"""

from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, List, Optional
from pydantic import BaseModel, Field

from segregator.domain.models import TaxRegime, EmploymentPeriod, EmploymentTypeKind


class PITConstants:
    """Нормативные ставки и лимиты подоходного налога в Польше (2024-2026)."""
    
    # Skala podatkowa
    KWOTA_WOLNA = Decimal('30000.00')           # 30 000 zł необлагаемый минимум
    KWOTA_ZMNIEJSZAJACA_ROCZNA = Decimal('3600.00') # 3 600 zł (12% от 30 000 zł)
    KWOTA_ZMNIEJSZAJACA_MIESIECZNA = Decimal('300.00') # 300 zł/мес при PIT-2
    PROG_PODATKOWY = Decimal('120000.00')       # Порог 120 000 zł
    STAWKA_SKALA_1 = Decimal('0.12')            # 12%
    STAWKA_SKALA_2 = Decimal('0.32')            # 32%
    
    # Podatek liniowy
    STAWKA_LINIOWY = Decimal('0.19')            # 19%
    LINIOWY_ZDROWOTNA_MAX_2025 = Decimal('12900.00') # Лимит вычета zdrowotna в 2025
    
    # UoP (Koszty uzyskania przychodu)
    KUP_UOP_BASIC_MONTHLY = Decimal('250.00')   # 250 zł/мес стандартные затраты
    KUP_UOP_COMMUTER_MONTHLY = Decimal('300.00')# 300 zł/мес для доезжающих
    
    # UZ
    KUP_UZ_PERCENT = Decimal('0.20')            # 20% KUP для договоров поручения
    
    # Взносы работника по UoP
    UOP_EMERYTALNE_PRAC = Decimal('0.0976')     # 9.76%
    UOP_RENTOWE_PRAC    = Decimal('0.0150')     # 1.50%
    UOP_CHOROBOWE_PRAC  = Decimal('0.0245')     # 2.45%
    UOP_ZDROWOTNA_PRAC  = Decimal('0.0900')     # 9.00%


class MonthlyTaxResult(BaseModel):
    """Результат расчета аванса по налогу за месяц."""
    month: str
    regime: TaxRegime
    income_ytd: Decimal       # Доход кумулятивно с начала года
    costs_ytd: Decimal        # Расходы кумулятивно с начала года
    tax_base_ytd: Decimal     # Налоговая база кумулятивно
    tax_due_ytd: Decimal      # Налог начисленный кумулятивно
    advances_paid_prior: Decimal # Ранее уплаченные авансы
    advance_to_pay: Decimal   # Аванс к уплате за данный месяц
    threshold_exceeded: bool = False # Превышен ли порог 120 000 zł
    notes: List[str] = Field(default_factory=list)


class PayrollResult(BaseModel):
    """Результат расчета заработной платы по UoP или UZ."""
    emp_type: EmploymentTypeKind
    gross_salary: Decimal
    employee_social_zus: Decimal # Складки ZUS работника
    zdrowotna_base: Decimal
    zdrowotna: Decimal
    tax_costs_kup: Decimal       # Затраты KUP
    tax_base: Decimal            # Налоговая база
    advance_pit: Decimal         # Аванс по налогу PIT
    net_salary: Decimal          # Зарплата на руки (netto)
    is_zero_pit_young: bool = False


class PITCalculator:
    """Детерминированный калькулятор подоходного налога и зарплаты."""

    @classmethod
    def calculate_skala_tax(cls, cumulative_tax_base: Decimal) -> Decimal:
        """
        Вычисляет годовой налог по Skala podatkowa от кумулятивной базы.
        - До 120 000 zł: 12% * база - 3 600 zł (kwota zmniejszająca)
        - Свыше 120 000 zł: 10 800 zł + 32% * (база - 120 000 zł)
        """
        if cumulative_tax_base <= Decimal('0.00'):
            return Decimal('0.00')

        if cumulative_tax_base <= PITConstants.PROG_PODATKOWY:
            tax = (cumulative_tax_base * PITConstants.STAWKA_SKALA_1) - PITConstants.KWOTA_ZMNIEJSZAJACA_ROCZNA
            return max(Decimal('0.00'), tax.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))
        else:
            base_12 = PITConstants.PROG_PODATKOWY
            base_32 = cumulative_tax_base - PITConstants.PROG_PODATKOWY
            tax_12 = (base_12 * PITConstants.STAWKA_SKALA_1) - PITConstants.KWOTA_ZMNIEJSZAJACA_ROCZNA # 10 800 zł
            tax_32 = base_32 * PITConstants.STAWKA_SKALA_2
            total_tax = tax_12 + tax_32
            return total_tax.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    @classmethod
    def calculate_monthly_jdg_advance(
        cls,
        month: str,
        regime: TaxRegime,
        income_ytd: Decimal,
        costs_ytd: Decimal,
        social_zus_paid_ytd: Decimal,
        health_zus_paid_ytd: Decimal = Decimal('0.00'),
        advances_paid_prior: Decimal = Decimal('0.00'),
        ryczalt_rate: Optional[Decimal] = None
    ) -> MonthlyTaxResult:
        """
        Кумулятивный расчет ежемесячного аванса по налогу для предпринимателя (JDG).
        """
        notes = []
        threshold_exceeded = False

        if regime == TaxRegime.SKALA:
            # База = Доходы - Расходы - Социальные взносы ZUS
            raw_base = income_ytd - costs_ytd - social_zus_paid_ytd
            tax_base_ytd = max(Decimal('0.00'), raw_base.quantize(Decimal('1'), rounding=ROUND_HALF_UP).quantize(Decimal('0.01')))
            
            tax_due_ytd = cls.calculate_skala_tax(tax_base_ytd)
            
            if tax_base_ytd > PITConstants.PROG_PODATKOWY:
                threshold_exceeded = True
                notes.append(f"Превышен порог 120 000 zł (база {tax_base_ytd} zł). Применена ставка 32% на сумму {tax_base_ytd - PITConstants.PROG_PODATKOWY} zł.")
                
        elif regime == TaxRegime.LINIOWY:
            # Вычет здоровья ограничен годовым лимитом
            deductible_health = min(health_zus_paid_ytd, PITConstants.LINIOWY_ZDROWOTNA_MAX_2025)
            raw_base = income_ytd - costs_ytd - social_zus_paid_ytd - deductible_health
            tax_base_ytd = max(Decimal('0.00'), raw_base.quantize(Decimal('1'), rounding=ROUND_HALF_UP).quantize(Decimal('0.01')))
            tax_due_ytd = (tax_base_ytd * PITConstants.STAWKA_LINIOWY).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            
        elif regime == TaxRegime.RYCZALT:
            # Налог от выручки за вычетом 50% уплаченной zdrowotna и 100% социальных
            rate = ryczalt_rate or Decimal('0.12')
            deductible_health_ryczalt = (health_zus_paid_ytd * Decimal('0.50')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            raw_base = income_ytd - social_zus_paid_ytd - deductible_health_ryczalt
            tax_base_ytd = max(Decimal('0.00'), raw_base.quantize(Decimal('1'), rounding=ROUND_HALF_UP).quantize(Decimal('0.01')))
            tax_due_ytd = (tax_base_ytd * rate).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        else:
            raise ValueError(f"Неподдерживаемый режим для JDG: {regime}")

        # Аванс к уплате = Налог кумулятивно - Ранее уплаченные авансы (округляется до полных злотых по закону Польши)
        advance_raw = max(Decimal('0.00'), tax_due_ytd - advances_paid_prior)
        advance_to_pay = advance_raw.quantize(Decimal('1'), rounding=ROUND_HALF_UP).quantize(Decimal('0.01'))

        return MonthlyTaxResult(
            month=month,
            regime=regime,
            income_ytd=income_ytd,
            costs_ytd=costs_ytd,
            tax_base_ytd=tax_base_ytd,
            tax_due_ytd=tax_due_ytd,
            advances_paid_prior=advances_paid_prior,
            advance_to_pay=advance_to_pay,
            threshold_exceeded=threshold_exceeded,
            notes=notes
        )

    @classmethod
    def calculate_uop_payroll(
        cls,
        gross_salary: Decimal,
        has_pit2: bool = True,
        is_commuter: bool = False,
        is_student_under_26: bool = False
    ) -> PayrollResult:
        """
        Расчет ежемесячной зарплаты по трудовому договору (Umowa o pracę).
        """
        if is_student_under_26:
            # Льгота для молодежи (Ulga dla młodych): 0% PIT до 85 528 zł в год
            is_zero_pit = True
        else:
            is_zero_pit = False

        # Социальные взносы работника
        emerytalne = (gross_salary * PITConstants.UOP_EMERYTALNE_PRAC).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        rentowe = (gross_salary * PITConstants.UOP_RENTOWE_PRAC).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        chorobowe = (gross_salary * PITConstants.UOP_CHOROBOWE_PRAC).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        total_social = emerytalne + rentowe + chorobowe

        # База для медицинского страхования
        zdrowotna_base = gross_salary - total_social
        zdrowotna = (zdrowotna_base * PITConstants.UOP_ZDROWOTNA_PRAC).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

        # Затраты KUP
        kup = PITConstants.KUP_UOP_COMMUTER_MONTHLY if is_commuter else PITConstants.KUP_UOP_BASIC_MONTHLY

        # База налогообложения (округляется до полных злотых)
        raw_tax_base = max(Decimal('0.00'), gross_salary - total_social - kup)
        tax_base = raw_tax_base.quantize(Decimal('1'), rounding=ROUND_HALF_UP).quantize(Decimal('0.01'))

        if is_zero_pit:
            advance_pit = Decimal('0.00')
        else:
            calc_pit = (tax_base * PITConstants.STAWKA_SKALA_1).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            if has_pit2:
                calc_pit -= PITConstants.KWOTA_ZMNIEJSZAJACA_MIESIECZNA
            advance_pit = max(Decimal('0.00'), calc_pit).quantize(Decimal('1'), rounding=ROUND_HALF_UP).quantize(Decimal('0.01'))

        # Зарплата на руки
        net_salary = gross_salary - total_social - zdrowotna - advance_pit

        return PayrollResult(
            emp_type=EmploymentTypeKind.UOP,
            gross_salary=gross_salary,
            employee_social_zus=total_social,
            zdrowotna_base=zdrowotna_base,
            zdrowotna=zdrowotna,
            tax_costs_kup=kup,
            tax_base=tax_base,
            advance_pit=advance_pit,
            net_salary=net_salary,
            is_zero_pit_young=is_zero_pit
        )

    @classmethod
    def calculate_uz_payroll(
        cls,
        gross_salary: Decimal,
        is_student_under_26: bool = False,
        pays_social: bool = True
    ) -> PayrollResult:
        """
        Расчет по договору поручения (Umowa zlecenie).
        Студенты до 26 лет освобождены от ZUS и PIT (Brutto = Netto).
        """
        if is_student_under_26:
            return PayrollResult(
                emp_type=EmploymentTypeKind.UZ,
                gross_salary=gross_salary,
                employee_social_zus=Decimal('0.00'),
                zdrowotna_base=Decimal('0.00'),
                zdrowotna=Decimal('0.00'),
                tax_costs_kup=Decimal('0.00'),
                tax_base=Decimal('0.00'),
                advance_pit=Decimal('0.00'),
                net_salary=gross_salary,
                is_zero_pit_young=True
            )

        if pays_social:
            emerytalne = (gross_salary * PITConstants.UOP_EMERYTALNE_PRAC).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            rentowe = (gross_salary * PITConstants.UOP_RENTOWE_PRAC).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            chorobowe = (gross_salary * PITConstants.UOP_CHOROBOWE_PRAC).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            total_social = emerytalne + rentowe + chorobowe
        else:
            total_social = Decimal('0.00')

        zdrowotna_base = gross_salary - total_social
        zdrowotna = (zdrowotna_base * PITConstants.UOP_ZDROWOTNA_PRAC).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

        # 20% KUP от суммы за вычетом соцвзносов
        kup = ((gross_salary - total_social) * PITConstants.KUP_UZ_PERCENT).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        tax_base = max(Decimal('0.00'), gross_salary - total_social - kup).quantize(Decimal('1'), rounding=ROUND_HALF_UP).quantize(Decimal('0.01'))

        # Аванс 12% округляется до полных злотых
        advance_pit = (tax_base * PITConstants.STAWKA_SKALA_1).quantize(Decimal('1'), rounding=ROUND_HALF_UP).quantize(Decimal('0.01'))
        net_salary = gross_salary - total_social - zdrowotna - advance_pit

        return PayrollResult(
            emp_type=EmploymentTypeKind.UZ,
            gross_salary=gross_salary,
            employee_social_zus=total_social,
            zdrowotna_base=zdrowotna_base,
            zdrowotna=zdrowotna,
            tax_costs_kup=kup,
            tax_base=tax_base,
            advance_pit=advance_pit,
            net_salary=net_salary,
            is_zero_pit_young=False
        )
