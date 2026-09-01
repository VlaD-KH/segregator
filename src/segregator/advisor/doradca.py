"""
src/segregator/advisor/doradca.py
Модуль налоговых симуляций и советника Agent-Doradca (Agent 04).
Выполняет детерминированное сравнение налоговых режимов (Skala vs Liniowy vs Ryczałt),
анализирует порог 120 000 zł и точку безубыточности перехода на Sp. z o.o. + CIT estoński.
Формирует обязательный дисклеймер в соответствии с польским законодательством (ТЗ §02).
"""

from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, List, Optional
from pydantic import BaseModel, Field

from src.segregator.tax.pit import PITCalculator, PITConstants
from src.segregator.domain.models import TaxRegime


class RegimeScenario(BaseModel):
    """Сценарий расчета налоговой нагрузки для одного режима."""
    regime_name: str                     # 'Skala podatkowa (12%/32%)' | 'Podatek liniowy (19%)' | 'Ryczałt (12%)'
    gross_revenue: Decimal
    tax_costs: Decimal
    social_zus: Decimal
    health_zus: Decimal
    pit_due: Decimal
    total_burden: Decimal                # Налог + ZUS
    net_income_on_hand: Decimal          # Чистый доход на руки
    effective_tax_rate_percent: Decimal
    pros: List[str] = Field(default_factory=list)
    cons: List[str] = Field(default_factory=list)


class AdvisoryReport(BaseModel):
    """
    Официальный отчет Agent-Doradca с результатами симуляции.
    Строго содержит scenarios (>=2), assumptions (>=1), unknowns и обязательный дисклеймер.
    """
    scenarios: List[RegimeScenario] = Field(default_factory=list)
    assumptions: List[str] = Field(default_factory=list)
    unknowns: List[str] = Field(default_factory=list)
    threshold_120k_analysis: Optional[str] = None
    sp_z_oo_analysis: Optional[str] = None
    disclaimer: str = (
        "Wszystkie powyższe wyliczenia mają charakter wyłącznie symulacyjny i informacyjny. "
        "Nie stanowią one porady podatkowej ani prawnej w rozumieniu Ustawy o doradztwie podatkowym. "
        "Ostateczną decyzję podejmuje przedsiębiorca po konsultacji z uprawnionym doradcą podatkowym."
    )


class AgentDoradca:
    """
    Детерминированный движок симуляций налоговых режимов.
    """

    @classmethod
    def compare_tax_regimes(
        cls,
        annual_revenue: Decimal,
        annual_costs: Decimal,
        ryczalt_rate: Decimal = Decimal('0.12'),
        social_zus_annual: Decimal = Decimal('4000.00'), # Пример годовых взносов
        year: int = 2025
    ) -> AdvisoryReport:
        """
        Проводит расчет и сравнение трех главных налоговых режимов Польши.
        """
        assumptions = [
            f"Roczne przychody: {annual_revenue} zł, roczne koszty (KUP): {annual_costs} zł.",
            f"Stawka Ryczałtu dla usług: {ryczalt_rate * 100}%.",
            f"Składki społeczne ZUS (odliczalne): {social_zus_annual} zł/rok.",
            "Brak dodatkowych ulg (ulga na dziecko, IKZE, IP BOX)."
        ]

        scenarios = []

        # -------------------------------------------------------------
        # 1. Сценарий A: Skala podatkowa (12% / 32%)
        # -------------------------------------------------------------
        skala_base = max(Decimal('0.00'), annual_revenue - annual_costs - social_zus_annual)
        skala_pit = PITCalculator.calculate_skala_tax(skala_base)
        # Zdrowotna на Skala = 9% от реального дохода
        skala_income = max(Decimal('0.00'), annual_revenue - annual_costs)
        skala_zdrowotna = (skala_income * Decimal('0.09')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        skala_burden = skala_pit + social_zus_annual + skala_zdrowotna
        skala_net = annual_revenue - annual_costs - skala_burden
        skala_eff_rate = ((skala_burden / annual_revenue) * Decimal('100')).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP) if annual_revenue > 0 else Decimal('0.0')

        scenarios.append(RegimeScenario(
            regime_name="Skala podatkowa (12% / 32%)",
            gross_revenue=annual_revenue,
            tax_costs=annual_costs,
            social_zus=social_zus_annual,
            health_zus=skala_zdrowotna,
            pit_due=skala_pit,
            total_burden=skala_burden,
            net_income_on_hand=skala_net,
            effective_tax_rate_percent=skala_eff_rate,
            pros=["Kwota wolna 30 000 zł (0% podatku)", "Wspólne rozliczenie z małżonkiem", "Odliczenie wszystkich kosztów KUP"],
            cons=["Stawка 32% powyżej 120 000 zł dochodu", "Wysoka składka zdrowotna 9% bez możliwości odliczenia"]
        ))

        # -------------------------------------------------------------
        # 2. Сценарий B: Podatek liniowy (19%)
        # -------------------------------------------------------------
        # На Liniowy: Zdrowotna 4.9% (можно списать в расходы до лимита 12 900 zł)
        liniowy_income = max(Decimal('0.00'), annual_revenue - annual_costs - social_zus_annual)
        liniowy_zdrowotna = (liniowy_income * Decimal('0.049')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        liniowy_base = max(Decimal('0.00'), liniowy_income - min(liniowy_zdrowotna, PITConstants.LINIOWY_ZDROWOTNA_MAX_2025))
        liniowy_pit = (liniowy_base * Decimal('0.19')).quantize(Decimal('1'), rounding=ROUND_HALF_UP).quantize(Decimal('0.01'))
        liniowy_burden = liniowy_pit + social_zus_annual + liniowy_zdrowotna
        liniowy_net = annual_revenue - annual_costs - liniowy_burden
        liniowy_eff_rate = ((liniowy_burden / annual_revenue) * Decimal('100')).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP) if annual_revenue > 0 else Decimal('0.0')

        scenarios.append(RegimeScenario(
            regime_name="Podatek liniowy (19%)",
            gross_revenue=annual_revenue,
            tax_costs=annual_costs,
            social_zus=social_zus_annual,
            health_zus=liniowy_zdrowotna,
            pit_due=liniowy_pit,
            total_burden=liniowy_burden,
            net_income_on_hand=liniowy_net,
            effective_tax_rate_percent=liniowy_eff_rate,
            pros=["Stała stawka 19% bez względu na wysokość dochodu", "Składka zdrowotna tylko 4.9% (częściowo odliczalna)"],
            cons=["Brak kwoty wolnej 30 000 zł", "Brak wspólnego rozliczenia z małżonkiem"]
        ))

        # -------------------------------------------------------------
        # 3. Сценарий C: Ryczałt ewidencjonowany
        # -------------------------------------------------------------
        # На Ryczałt: база = выручка (расходы не учитываются)
        # Здоровтна = фиксированная ставка по порогам выручки (для 60k-300k zł ~ 730 zł/мес = 8 760 zł/год)
        ryczalt_zdrowotna = Decimal('8760.00')
        # 50% zdrowotna можно отнять от базы ryczałt
        ryczalt_base = max(Decimal('0.00'), annual_revenue - social_zus_annual - (ryczalt_zdrowotna * Decimal('0.5')))
        ryczalt_pit = (ryczalt_base * ryczalt_rate).quantize(Decimal('1'), rounding=ROUND_HALF_UP).quantize(Decimal('0.01'))
        ryczalt_burden = ryczalt_pit + social_zus_annual + ryczalt_zdrowotna
        # Для чистого дохода на руки реальные расходы все равно тратятся!
        ryczalt_net = annual_revenue - annual_costs - ryczalt_burden
        ryczalt_eff_rate = ((ryczalt_burden / annual_revenue) * Decimal('100')).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP) if annual_revenue > 0 else Decimal('0.0')

        scenarios.append(RegimeScenario(
            regime_name=f"Ryczałt ewidencjonowany ({ryczalt_rate*100}%)",
            gross_revenue=annual_revenue,
            tax_costs=annual_costs,
            social_zus=social_zus_annual,
            health_zus=ryczalt_zdrowotna,
            pit_due=ryczalt_pit,
            total_burden=ryczalt_burden,
            net_income_on_hand=ryczalt_net,
            effective_tax_rate_percent=ryczalt_eff_rate,
            pros=[f"Niska stawka podatku {ryczalt_rate*100}% od przychodu", "Uproszczona ewidencja przychodów"],
            cons=["Całkowity zakaz odliczania kosztów KUP", "Rzeczywiste wydatki obniżają zysk netto bez ulgi podatkowej"]
        ))

        # Анализ 120 000 PLN
        thresh_info = (
            f"Przy dochodzie {skala_income} zł: "
            + ("przekraczasz I próg skali (120 000 zł), co powoduje opodatkowanie nadwyżki stawką 32%." if skala_income > Decimal('120000.00') else "mieścisz się w I progu skali (12%).")
        )

        # Анализ Sp. z o.o. + Estonski CIT
        sp_info = (
            "Przejście na Sp. z o.o. z Estońskim CIT (9% dla małego podatnika) staje się opłacalne przy zyskach > 250 000 zł/rok "
            "i reinwestowaniu zysków w firmę. Należy uwzględнить dodatkowy koszt pełnej księgowości (~800-1500 zł/mc) oraz sprawozdawczości KRS."
        )

        return AdvisoryReport(
            scenarios=scenarios,
            assumptions=assumptions,
            unknowns=["Plany inwestycyjne w środki trwałe w kolejnym kwartale", "Możliwość zastosowania ulgi IP BOX (5%)"],
            threshold_120k_analysis=thresh_info,
            sp_z_oo_analysis=sp_info
        )
