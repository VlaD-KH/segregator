"""
src/segregator/domain/zus.py
Детерминированный калькулятор обязательств ZUS (Social Security) для Польши.
Строгая арифметика на базе Decimal. Никаких вычислений в LLM.
"""

from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, Optional

from src.segregator.domain.models import (
    EmploymentPeriod,
    EmploymentType,
    TaxpayerProfile,
    TaxRegime,
    ZUSObligations,
    ZUSStage,
)


class ZUSConstants:
    """Нормативные константы минимальной и средней заработной платы в Польше."""
    
    # Минимальная зарплата (Minimalne wynagrodzenie brutto)
    MINIMAL_WAGES: Dict[int, Decimal] = {
        2024: Decimal('4300.00'),
        2025: Decimal('4666.00'),
        2026: Decimal('4800.00'),
    }
    
    # Прогнозируемая средняя зарплата (Prognozowane przeciętne wynagrodzenie)
    AVERAGE_WAGES: Dict[int, Decimal] = {
        2024: Decimal('7824.00'),
        2025: Decimal('8673.00'),
        2026: Decimal('9200.00'),
    }
    
    # Ставки социальных взносов
    RATE_EMERYTALNE = Decimal('0.1952')   # 19.52% (Пенсионное)
    RATE_RENTOWE    = Decimal('0.0800')   # 8.00% (Инвалидное)
    RATE_CHOROBOWE  = Decimal('0.0245')   # 2.45% (Больничное - добровольное)
    RATE_WYPADKOWE  = Decimal('0.0167')   # 1.67% (От несчастных случаев)
    RATE_FP_FS      = Decimal('0.0245')   # 2.45% (Фонд труда и солидарности)
    
    RATE_ZDROWOTNA_SKALA   = Decimal('0.0900') # 9.0%
    RATE_ZDROWOTNA_LINIOWY = Decimal('0.0490') # 4.9%
    
    @classmethod
    def get_minimal_wage(cls, year: int) -> Decimal:
        return cls.MINIMAL_WAGES.get(year, cls.MINIMAL_WAGES[2025])
        
    @classmethod
    def get_average_wage(cls, year: int) -> Decimal:
        return cls.AVERAGE_WAGES.get(year, cls.AVERAGE_WAGES[2025])


class ZUSCalculator:
    """
    Детерминированный калькулятор обязательств ZUS.
    Реализует хронологию льгот и Zbieg tytułów ubezpieczeń.
    """

    @staticmethod
    def determine_zus_stage(jdg_start_date: date, target_date: date) -> ZUSStage:
        """
        Определяет стадию ZUS на целевую дату.
        Правило 6 месяцев Ulga na start:
        - Если старт 1-го числа: ровно 6 месяцев (с месяца старта по месяц +5).
        - Если старт >1-го числа: текущий неполный месяц + 6 следующих полных месяцев.
        """
        if target_date < jdg_start_date:
            raise ValueError(f"Целевая дата {target_date} предшествует дате открытия JDG {jdg_start_date}")

        # Разница в месяцах
        months_diff = (target_date.year - jdg_start_date.year) * 12 + (target_date.month - jdg_start_date.month)
        
        # Если старт не 1-го числа месяца, первый месяц "бонусный" и не уменьшает лимит 6 месяцев
        effective_elapsed_months = months_diff if jdg_start_date.day == 1 else months_diff - 1

        if effective_elapsed_months < 6:
            return ZUSStage.ULGA_NA_START
        elif effective_elapsed_months < (6 + 24):
            return ZUSStage.PREFERENCYJNY
        else:
            return ZUSStage.DUZY_ZUS

    @staticmethod
    def check_zbieg_tytulow(profile: TaxpayerProfile, target_date: date) -> bool:
        """
        Проверяет пересечение титулов (Zbieg tytułów ubezpieczeń).
        Если в целевом месяце действует трудовой договор (UoP) с окладом >= минимальной зарплаты,
        предприниматель освобождается от социальных взносов в JDG.
        """
        min_wage = ZUSConstants.get_minimal_wage(target_date.year)
        
        for period in profile.employment_history:
            if period.emp_type == EmploymentType.UOP:
                # Проверка активности периода
                is_after_start = target_date >= period.start_date
                is_before_end = period.end_date is None or target_date <= period.end_date
                
                if is_after_start and is_before_end:
                    if period.monthly_gross_avg >= min_wage:
                        return True
                        
        return False

    @classmethod
    def calculate_monthly_obligations(
        cls,
        profile: TaxpayerProfile,
        target_month: date,
        jdg_monthly_profit: Decimal = Decimal('0.00'),
        include_chorobowe: bool = True
    ) -> ZUSObligations:
        """
        Вычисляет точные суммы взносов ZUS за указанный месяц с точностью до гроша.
        """
        # Поиск периода JDG
        jdg_period = next((p for p in profile.employment_history if p.emp_type == EmploymentType.JDG), None)
        
        if not jdg_period:
            raise ValueError("В профиле налогоплательщика не найден активный период JDG")
            
        stage = cls.determine_zus_stage(jdg_period.start_date, target_month)
        has_zbieg = cls.check_zbieg_tytulow(profile, target_month)
        
        year = target_month.year
        min_wage = ZUSConstants.get_minimal_wage(year)
        avg_wage = ZUSConstants.get_average_wage(year)
        
        month_str = target_month.strftime("%Y-%m")
        obligations = ZUSObligations(
            month=month_str,
            stage=stage,
            zbieg_tytulow=has_zbieg
        )
        
        # Определяем необходимость уплаты социальных взносов
        # Социальные взносы НЕ платятся при Zbieg tytułów или на стадии Ulga na start
        must_pay_social = not (has_zbieg or stage == ZUSStage.ULGA_NA_START)
        
        if must_pay_social:
            if stage == ZUSStage.PREFERENCYJNY:
                # База: 30% от минимальной зарплаты
                base_spoleczne = (min_wage * Decimal('0.30')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
                pays_fp = False # На преференции FP не платится
            elif stage == ZUSStage.DUZY_ZUS:
                # База: 60% от средней зарплаты
                base_spoleczne = (avg_wage * Decimal('0.60')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
                pays_fp = True
            else: # MALY_ZUS_PLUS
                base_spoleczne = (min_wage * Decimal('0.30')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
                pays_fp = False
                
            obligations.spoleczne_base = base_spoleczne
            obligations.emerytalne = (base_spoleczne * ZUSConstants.RATE_EMERYTALNE).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            obligations.rentowe = (base_spoleczne * ZUSConstants.RATE_RENTOWE).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            obligations.wypadkowe = (base_spoleczne * ZUSConstants.RATE_WYPADKOWE).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            
            if include_chorobowe:
                obligations.chorobowe = (base_spoleczne * ZUSConstants.RATE_CHOROBOWE).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
                
            if pays_fp:
                obligations.fundusz_pracy = (base_spoleczne * ZUSConstants.RATE_FP_FS).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
                
            obligations.total_spoleczne = (
                obligations.emerytalne +
                obligations.rentowe +
                obligations.wypadkowe +
                obligations.chorobowe +
                obligations.fundusz_pracy
            )
        else:
            obligations.spoleczne_base = Decimal('0.00')

        # Расчет взноса на медицинское страхование (Składka Zdrowotna) - платится ВСЕГДА
        min_zdrowotna = (min_wage * Decimal('0.09')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        tax_regime = profile.jdg_tax_regime or TaxRegime.SKALA
        
        if tax_regime == TaxRegime.SKALA:
            calc_zdrowotna = (jdg_monthly_profit * ZUSConstants.RATE_ZDROWOTNA_SKALA).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            obligations.skladka_zdrowotna = max(min_zdrowotna, calc_zdrowotna)
            obligations.zdrowotna_base = max(min_wage, jdg_monthly_profit)
        elif tax_regime == TaxRegime.LINIOWY:
            calc_zdrowotna = (jdg_monthly_profit * ZUSConstants.RATE_ZDROWOTNA_LINIOWY).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            obligations.skladka_zdrowotna = max(min_zdrowotna, calc_zdrowotna)
            obligations.zdrowotna_base = max(min_wage, jdg_monthly_profit)
        else: # RYCZALT
            # Фиксированная база в зависимости от порога годовой выручки (по умолчанию базовый порог)
            ryczalt_base = (avg_wage * Decimal('0.60')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            obligations.zdrowotna_base = ryczalt_base
            obligations.skladka_zdrowotna = (ryczalt_base * Decimal('0.09')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

        # Итоговая сумма к уплате
        obligations.total_zus_do_zaplaty = obligations.total_spoleczne + obligations.skladka_zdrowotna
        
        # Формуляры к подаче
        if stage == ZUSStage.ULGA_NA_START or has_zbieg:
            obligations.forms_required = ["ZUS DRA", "ZUS ZZA"]
        else:
            obligations.forms_required = ["ZUS DRA", "ZUS RCA"]
            
        return obligations
