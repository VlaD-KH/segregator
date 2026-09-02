"""
src/segregator/domain/zus.py
Детерминированный калькулятор обязательств ZUS (Social Security) для Польши.
Строгая арифметика на базе Decimal. Отказ от расчетов для неизвестных годов.
"""

from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, Optional

from segregator.domain.models import (
    EmploymentPeriod,
    EmploymentTypeKind,
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
        if year not in cls.MINIMAL_WAGES:
            raise ValueError(f"Brak stawek minimalnego wynagrodzenia dla roku {year}. Odmowa kalkulacji.")
        return cls.MINIMAL_WAGES[year]
        
    @classmethod
    def get_average_wage(cls, year: int) -> Decimal:
        if year not in cls.AVERAGE_WAGES:
            raise ValueError(f"Brak stawek przeciętnego wynagrodzenia dla roku {year}. Odmowa kalkulacji.")
        return cls.AVERAGE_WAGES[year]


class ZUSCalculator:
    """
    Детерминированный калькулятор обязательств ZUS.
    Реализует хронологию льгот и Zbieg tytułów ubezpieczeń.
    """

    @classmethod
    def determine_zus_stage(cls, jdg_start_date: date, target_month: date) -> ZUSStage:
        """
        Определяет этап ZUS на основе даты открытия JDG и целевого месяца.
        """
        if target_month < jdg_start_date:
            return ZUSStage.BRAK

        # Подсчет полных месяцев
        months_diff = (target_month.year - jdg_start_date.year) * 12 + (target_month.month - jdg_start_date.month)
        
        # Правило: Если JDG открыто не 1-го числа, текущий неполный месяц — бонусный
        if jdg_start_date.day > 1:
            if months_diff <= 6:
                return ZUSStage.ULGA_NA_START
            elif months_diff <= 30:
                return ZUSStage.PREFERENCYJNY
            else:
                return ZUSStage.DUZY_ZUS
        else:
            if months_diff < 6:
                return ZUSStage.ULGA_NA_START
            elif months_diff < 30:
                return ZUSStage.PREFERENCYJNY
            else:
                return ZUSStage.DUZY_ZUS

    @classmethod
    def check_zbieg_tytulow(cls, profile: TaxpayerProfile, target_month: date) -> bool:
        """
        Проверяет Zbieg tytułów ubezpieczeń (параллельная работа по найму).
        """
        min_wage = ZUSConstants.get_minimal_wage(target_month.year)
        
        for p in profile.employment_history:
            if p.emp_type == EmploymentTypeKind.UOP:
                # Проверяем, активен ли UoP в целевом месяце
                if p.start_date <= target_month:
                    if p.end_date is None or p.end_date >= target_month:
                        if p.monthly_gross_avg >= min_wage:
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
        Рассчитывает ежемесячные суммы взносов ZUS.
        """
        # Поиск периода JDG в профиле
        jdg_period = next((p for p in profile.employment_history if p.emp_type == EmploymentTypeKind.JDG), None)
        if not jdg_period:
            return ZUSObligations(
                stage=ZUSStage.BRAK,
                month=target_month.strftime("%Y-%m")
            )

        year = target_month.year
        month_str = target_month.strftime("%Y-%m")
        min_wage = ZUSConstants.get_minimal_wage(year)
        avg_wage = ZUSConstants.get_average_wage(year)

        stage = cls.determine_zus_stage(jdg_period.start_date, target_month)
        has_zbieg = cls.check_zbieg_tytulow(profile, target_month)

        spoleczne_base = Decimal('0.00')
        emerytalne = Decimal('0.00')
        rentowe = Decimal('0.00')
        chorobowe = Decimal('0.00')
        wypadkowe = Decimal('0.00')
        fp = Decimal('0.00')
        forms = ["ZUS DRA"]

        # Если действует Ulga na start или есть Zbieg tytułów -> социальные взносы = 0
        if stage == ZUSStage.ULGA_NA_START or has_zbieg:
            spoleczne_base = Decimal('0.00')
            forms.append("ZUS ZZA")
        elif stage == ZUSStage.PREFERENCYJNY:
            spoleczne_base = (min_wage * Decimal('0.30')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            emerytalne = (spoleczne_base * ZUSConstants.RATE_EMERYTALNE).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            rentowe = (spoleczne_base * ZUSConstants.RATE_RENTOWE).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            if include_chorobowe:
                chorobowe = (spoleczne_base * ZUSConstants.RATE_CHOROBOWE).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            wypadkowe = (spoleczne_base * ZUSConstants.RATE_WYPADKOWE).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            forms.append("ZUS ZUA")
        elif stage == ZUSStage.DUZY_ZUS:
            spoleczne_base = (avg_wage * Decimal('0.60')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            emerytalne = (spoleczne_base * ZUSConstants.RATE_EMERYTALNE).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            rentowe = (spoleczne_base * ZUSConstants.RATE_RENTOWE).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            if include_chorobowe:
                chorobowe = (spoleczne_base * ZUSConstants.RATE_CHOROBOWE).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            wypadkowe = (spoleczne_base * ZUSConstants.RATE_WYPADKOWE).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            fp = (spoleczne_base * ZUSConstants.RATE_FP_FS).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            forms.append("ZUS ZUA")

        total_spoleczne = emerytalne + rentowe + chorobowe + wypadkowe

        # Расчет взноса на медицинское страхование (Składka zdrowotna)
        zdrowotna_base = Decimal('0.00')
        zdrowotna = Decimal('0.00')
        
        regime = profile.jdg_tax_regime or TaxRegime.SKALA
        min_zdrowotna = (min_wage * Decimal('0.09')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

        if regime == TaxRegime.SKALA:
            zdrowotna_base = max(Decimal('0.00'), jdg_monthly_profit)
            zdrowotna = max(min_zdrowotna, (zdrowotna_base * ZUSConstants.RATE_ZDROWOTNA_SKALA).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))
        elif regime == TaxRegime.LINIOWY:
            zdrowotna_base = max(Decimal('0.00'), jdg_monthly_profit)
            zdrowotna = max(min_zdrowotna, (zdrowotna_base * ZUSConstants.RATE_ZDROWOTNA_LINIOWY).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))
        elif regime == TaxRegime.RYCZALT:
            # Для Ryczałt 9% от 60%/100%/180% средней зарплаты в IV кв.
            zdrowotna_base = (avg_wage * Decimal('1.00')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            zdrowotna = (zdrowotna_base * Decimal('0.09')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

        total_do_zaplaty = total_spoleczne + fp + zdrowotna

        return ZUSObligations(
            stage=stage,
            month=month_str,
            spoleczne_base=spoleczne_base,
            zdrowotna_base=zdrowotna_base,
            emerytalne=emerytalne,
            rentowe=rentowe,
            chorobowe=chorobowe,
            wypadkowe=wypadkowe,
            fundusz_pracy=fp,
            skladka_zdrowotna=zdrowotna,
            total_spoleczne=total_spoleczne,
            total_zus_do_zaplaty=total_do_zaplaty,
            forms_required=forms,
            zbieg_tytulow=has_zbieg
        )
