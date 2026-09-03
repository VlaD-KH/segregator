"""
src/segregator/advisor/doradca.py
Модуль налоговых симуляций и советника Agent-Doradca (Agent 04).
Выполняет детерминированное сравнение налоговых режимов (Skala vs Liniowy vs Ryczałt),
анализирует порог 120 000 zł и точку безубыточности перехода на Sp. z o.o. + CIT estoński.
100% совместимость с agents/schemas/advisory_report.json.
"""

from decimal import Decimal, ROUND_HALF_UP
from typing import List

from segregator.tax.pit import PITCalculator, PITConstants
from segregator.domain.zus import ZUSConstants
from segregator.domain.models import DISCLAIMER_PL, AdvisoryReport, AdvisoryScenarioItem


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
        social_zus_annual: Decimal = Decimal('4000.00'),
        year: int = 2025
    ) -> AdvisoryReport:
        """
        Проводит расчет и сравнение трех главных налоговых режимов Польши.
        Возвращает AdvisoryReport, строго валидный против agents/schemas/advisory_report.json.

        `year` больше не декоративный: год без нормативных таблиц приводит к
        отказу считать (ValueError), а не к молчаливому счёту по числам 2025.
        """
        assumptions = [
            f"Roczne przychody: {annual_revenue} zł, roczne koszty (KUP): {annual_costs} zł.",
            f"Stawka Ryczałtu dla usług: {ryczalt_rate * 100}%.",
            f"Składki społeczne ZUS (odliczalne): {social_zus_annual} zł/rok.",
            "Brak dodatkowych ulg (ulga na dziecko, IKZE, IP BOX)."
        ]

        scenarios: List[AdvisoryScenarioItem] = []

        # -------------------------------------------------------------
        # 1. Сценарий A: Skala podatkowa (12% / 32%)
        # -------------------------------------------------------------
        skala_base = max(Decimal('0.00'), annual_revenue - annual_costs - social_zus_annual)
        skala_pit = PITCalculator.calculate_skala_tax(skala_base)
        skala_income = max(Decimal('0.00'), annual_revenue - annual_costs)
        skala_zdrowotna = (skala_income * Decimal('0.09')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        skala_burden = skala_pit + social_zus_annual + skala_zdrowotna
        skala_net = annual_revenue - annual_costs - skala_burden
        skala_eff_rate = float(((skala_burden / annual_revenue) * Decimal('100')).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)) if annual_revenue > 0 else 0.0

        scenarios.append(AdvisoryScenarioItem(
            name="Skala podatkowa (12% / 32%)",
            figures={
                "przychod": float(annual_revenue),
                "koszty": float(annual_costs),
                "podatek_pit": float(skala_pit),
                "skladki_zus": float(social_zus_annual + skala_zdrowotna),
                "zysk_netto": float(skala_net)
            },
            effective_burden_pct=skala_eff_rate,
            tradeoff="Stawka 32% powyżej progu 120 000 zł dochodu oraz wysoka nieodliczalna składka zdrowotna 9%."
        ))

        # -------------------------------------------------------------
        # 2. Сценарий B: Podatek liniowy (19%)
        # -------------------------------------------------------------
        liniowy_income = max(Decimal('0.00'), annual_revenue - annual_costs - social_zus_annual)
        liniowy_zdrowotna = (liniowy_income * Decimal('0.049')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        liniowy_zdrowotna_limit = PITConstants.get_liniowy_zdrowotna_max(year)
        liniowy_base = max(Decimal('0.00'), liniowy_income - min(liniowy_zdrowotna, liniowy_zdrowotna_limit))
        liniowy_pit = (liniowy_base * Decimal('0.19')).quantize(Decimal('1'), rounding=ROUND_HALF_UP).quantize(Decimal('0.01'))
        liniowy_burden = liniowy_pit + social_zus_annual + liniowy_zdrowotna
        liniowy_net = annual_revenue - annual_costs - liniowy_burden
        liniowy_eff_rate = float(((liniowy_burden / annual_revenue) * Decimal('100')).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)) if annual_revenue > 0 else 0.0

        scenarios.append(AdvisoryScenarioItem(
            name="Podatek liniowy (19%)",
            figures={
                "przychod": float(annual_revenue),
                "koszty": float(annual_costs),
                "podatek_pit": float(liniowy_pit),
                "skladki_zus": float(social_zus_annual + liniowy_zdrowotna),
                "zysk_netto": float(liniowy_net)
            },
            effective_burden_pct=liniowy_eff_rate,
            tradeoff="Brak kwoty wolnej 30 000 zł i brak możliwości wspólnego rozliczenia z małżonkiem."
        ))

        # -------------------------------------------------------------
        # 3. Сценарий C: Ryczałt ewidencjonowany
        # -------------------------------------------------------------
        # Годовая składka zdrowotna на ryczałcie: 12 × (ступень базы × 9%).
        # Ступень выбирается по годовому przychodowi (60k / 300k), база — это
        # przeciętne wynagrodzenie w sektorze przedsiębiorstw за IV кв. прошлого
        # года. Раньше здесь стояло фиксированное 8760.00 без года и без ступеней.
        # TODO(Фаза 4): таблица переезжает в rates/<year>.toml.
        ryczalt_zdrowotna_base = ZUSConstants.get_ryczalt_zdrowotna_base(year, annual_revenue)
        ryczalt_zdrowotna = (
            (ryczalt_zdrowotna_base * ZUSConstants.RATE_ZDROWOTNA_SKALA)
            .quantize(Decimal('0.01'), rounding=ROUND_HALF_UP) * 12
        )
        ryczalt_base = max(Decimal('0.00'), annual_revenue - social_zus_annual - (ryczalt_zdrowotna * Decimal('0.5')))
        ryczalt_pit = (ryczalt_base * ryczalt_rate).quantize(Decimal('1'), rounding=ROUND_HALF_UP).quantize(Decimal('0.01'))
        ryczalt_burden = ryczalt_pit + social_zus_annual + ryczalt_zdrowotna
        ryczalt_net = annual_revenue - annual_costs - ryczalt_burden
        ryczalt_eff_rate = float(((ryczalt_burden / annual_revenue) * Decimal('100')).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)) if annual_revenue > 0 else 0.0

        scenarios.append(AdvisoryScenarioItem(
            name=f"Ryczałt ewidencjonowany ({ryczalt_rate*100}%)",
            figures={
                "przychod": float(annual_revenue),
                "koszty": float(annual_costs),
                "podatek_pit": float(ryczalt_pit),
                "skladki_zus": float(social_zus_annual + ryczalt_zdrowotna),
                "zysk_netto": float(ryczalt_net)
            },
            effective_burden_pct=ryczalt_eff_rate,
            tradeoff="Całkowity zakaz odliczania kosztów uzyskania przychodu (KUP). Rzeczywiste wydatki obniżają zysk netto."
        ))

        note = (
            f"Analiza progów: Dochód wynosi {skala_income} zł. "
            + ("Przekroczono próg 120 000 zł na skali. " if skala_income > Decimal('120000.00') else "Dochód mieści się w I progu 12%. ")
            # Ставки эстонского CIT — 10% для małego podatnika i rozpoczynającego
            # działalność, 20% для остальных (art. 28o ustawy o CIT). 9% — это
            # обычный CIT для małych podatników (art. 19 ust. 1 pkt 2), другой режим.
            + "Przejście na Sp. z o.o. (Estoński CIT: 10% dla małego podatnika, "
            + "20% dla pozostałych) warto rozważyć przy zyskach powyżej 250 000 zł rocznie."
        )

        return AdvisoryReport(
            scenarios=scenarios,
            assumptions=assumptions,
            unknowns=["Plany zakupowe środków trwałych w kolejnych kwartałach", "Prawo do ulgi IP BOX"],
            note=note[:900],
            disclaimer=DISCLAIMER_PL,
        )
