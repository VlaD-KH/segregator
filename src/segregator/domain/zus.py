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
    
    # Минимальная зарплата (Minimalne wynagrodzenie brutto).
    # 2024 — значение II полугодия; за I полугодие действовало 4242.00,
    # разбивки по полугодиям здесь нет (см. TODO Фазы 4).
    MINIMAL_WAGES: Dict[int, Decimal] = {
        2024: Decimal('4300.00'),   # Dz.U. 2023 poz. 1893
        2025: Decimal('4666.00'),   # Dz.U. 2024 poz. 1362
        2026: Decimal('4806.00'),   # Dz.U. 2025 poz. 1242, в силе с 2026-01-01
    }

    # Прогнозируемая средняя зарплата (Prognozowane przeciętne wynagrodzenie).
    # База dużego ZUS = 60% от этой величины.
    AVERAGE_WAGES: Dict[int, Decimal] = {
        2024: Decimal('7824.00'),   # ustawa budżetowa na 2024
        2025: Decimal('8673.00'),   # ustawa budżetowa na 2025
        2026: Decimal('9420.00'),   # ustawa budżetowa na 2026 (база 5652.00)
    }

    # Składka zdrowotna на ryczałcie считается НЕ от prognozowanego, а от
    # przeciętnego wynagrodzenia w sektorze przedsiębiorstw за IV квартал
    # ПРЕДЫДУЩЕГО года (obwieszczenie Prezesa GUS). Показатель другой —
    # смешивать их нельзя.
    RYCZALT_ZDROWOTNA_WAGES: Dict[int, Decimal] = {
        2025: Decimal('8549.18'),   # IV кв. 2024
        2026: Decimal('9228.64'),   # IV кв. 2025
    }

    # Пороги годового przychodu, по которым выбирается ступень базы (art. 81 ust. 2e-2g)
    RYCZALT_PROG_1 = Decimal('60000.00')
    RYCZALT_PROG_2 = Decimal('300000.00')
    RYCZALT_BASE_RATIOS = (Decimal('0.60'), Decimal('1.00'), Decimal('1.80'))

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

    @classmethod
    def get_ryczalt_zdrowotna_base(cls, year: int, annual_revenue: Decimal) -> Decimal:
        """База składki zdrowotnej на ryczałcie: 60% / 100% / 180% по порогам przychodu."""
        if year not in cls.RYCZALT_ZDROWOTNA_WAGES:
            raise ValueError(
                f"Brak przeciętnego wynagrodzenia w sektorze przedsiębiorstw dla roku {year}. "
                f"Odmowa kalkulacji składki zdrowotnej na ryczałcie."
            )
        wage = cls.RYCZALT_ZDROWOTNA_WAGES[year]
        if annual_revenue <= cls.RYCZALT_PROG_1:
            ratio = cls.RYCZALT_BASE_RATIOS[0]
        elif annual_revenue <= cls.RYCZALT_PROG_2:
            ratio = cls.RYCZALT_BASE_RATIOS[1]
        else:
            ratio = cls.RYCZALT_BASE_RATIOS[2]
        return (wage * ratio).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


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

    @staticmethod
    def _previous_month(d: date) -> date:
        return date(d.year - 1, 12, 1) if d.month == 1 else date(d.year, d.month - 1, 1)

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
        include_chorobowe: bool = True,
        annual_revenue: Optional[Decimal] = None,
    ) -> ZUSObligations:
        """Рассчитывает ежемесячные суммы взносов ZUS.

        ``annual_revenue`` — годовой przychód, обязателен только для ryczałtu:
        от него зависит ступень базы składki zdrowotnej (60/100/180%). Для
        остальных режимов не используется.
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

        # Месяц до открытия JDG: обязательств нет вообще. Раньше сюда доходил
        # расчёт zdrowotnej — взнос начислялся за месяц без деятельности.
        if stage == ZUSStage.BRAK:
            return ZUSObligations(stage=ZUSStage.BRAK, month=month_str)

        has_zbieg = cls.check_zbieg_tytulow(profile, target_month)

        spoleczne_base = Decimal('0.00')
        emerytalne = Decimal('0.00')
        rentowe = Decimal('0.00')
        chorobowe = Decimal('0.00')
        wypadkowe = Decimal('0.00')
        fp = Decimal('0.00')

        # Месячная отчётность JDG без работников — это ZUS DRA, и только он.
        # RCA — именной отчёт ЗА застрахованных лиц, płatnikowi «сам за себя» не нужен.
        # ZUA/ZZA/ZWUA — регистрационные формы: подаются в месяц смены титула
        # страхования (старт JDG или переход между ступенями), а не ежемесячно.
        forms = ["ZUS DRA"]
        prev_stage = cls.determine_zus_stage(
            jdg_period.start_date, cls._previous_month(target_month)
        )
        if prev_stage != stage:
            if prev_stage != ZUSStage.BRAK:
                forms.append("ZUS ZWUA")  # снятие прежнего кода титула
            forms.append(
                "ZUS ZZA" if (stage == ZUSStage.ULGA_NA_START or has_zbieg) else "ZUS ZUA"
            )

        # Если действует Ulga na start или есть Zbieg tytułów -> социальные взносы = 0
        if stage == ZUSStage.ULGA_NA_START or has_zbieg:
            spoleczne_base = Decimal('0.00')
        elif stage == ZUSStage.PREFERENCYJNY:
            spoleczne_base = (min_wage * Decimal('0.30')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            emerytalne = (spoleczne_base * ZUSConstants.RATE_EMERYTALNE).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            rentowe = (spoleczne_base * ZUSConstants.RATE_RENTOWE).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            if include_chorobowe:
                chorobowe = (spoleczne_base * ZUSConstants.RATE_CHOROBOWE).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            wypadkowe = (spoleczne_base * ZUSConstants.RATE_WYPADKOWE).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        elif stage == ZUSStage.DUZY_ZUS:
            spoleczne_base = (avg_wage * Decimal('0.60')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            emerytalne = (spoleczne_base * ZUSConstants.RATE_EMERYTALNE).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            rentowe = (spoleczne_base * ZUSConstants.RATE_RENTOWE).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            if include_chorobowe:
                chorobowe = (spoleczne_base * ZUSConstants.RATE_CHOROBOWE).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            wypadkowe = (spoleczne_base * ZUSConstants.RATE_WYPADKOWE).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            fp = (spoleczne_base * ZUSConstants.RATE_FP_FS).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

        # Fundusz Pracy сюда НЕ входит: в декларации DRA это отдельный блок
        # (VIII/p1 и IX/p3 против IX/p1), а в вычет по PIT идут только składki
        # społeczne. Свернуть их вместе — двойной счёт в KEDU-XML.
        total_spoleczne = emerytalne + rentowe + chorobowe + wypadkowe

        # Расчет взноса на медицинское страхование (Składka zdrowotna)
        zdrowotna_base = Decimal('0.00')
        zdrowotna = Decimal('0.00')
        
        regime = profile.jdg_tax_regime or TaxRegime.SKALA

        if regime in (TaxRegime.SKALA, TaxRegime.LINIOWY):
            # Минимум по art. 81 ust. 2j — это пол для БАЗЫ, а не для готового взноса.
            # Прежний пол `min_wage * 9%` применялся и к liniowemu со ставкой 4.9%,
            # завышая минимальный взнос почти вдвое (419.94 вместо 228.63 за 2025).
            zdrowotna_base = max(min_wage, jdg_monthly_profit)
            rate = (
                ZUSConstants.RATE_ZDROWOTNA_SKALA
                if regime == TaxRegime.SKALA
                else ZUSConstants.RATE_ZDROWOTNA_LINIOWY
            )
            zdrowotna = (zdrowotna_base * rate).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        elif regime == TaxRegime.RYCZALT:
            # TODO(Фаза 4): ставки и пороги переезжают в rates/<year>.toml вместе с
            # ELI-ссылкой и датой сверки. Пока таблица зашита в ZUSConstants —
            # черновой вариант, к нему нужно вернуться.
            if annual_revenue is None:
                raise ValueError(
                    "Dla ryczałtu wymagany jest roczny przychód (annual_revenue): "
                    "od niego zależy próg podstawy składki zdrowotnej. Odmowa kalkulacji."
                )
            zdrowotna_base = ZUSConstants.get_ryczalt_zdrowotna_base(year, annual_revenue)
            zdrowotna = (zdrowotna_base * ZUSConstants.RATE_ZDROWOTNA_SKALA).quantize(
                Decimal('0.01'), rounding=ROUND_HALF_UP
            )

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
