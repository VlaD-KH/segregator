"""
tests/test_advisor_doradca.py
Тесты для советника Agent-Doradca (Agent 04) и детерминированных симуляций налоговых режимов.
"""

from decimal import Decimal
import pytest

from src.segregator.advisor.doradca import AgentDoradca


def test_agent_doradca_regime_comparison():
    """
    Тест сравнения налоговых режимов Skala, Liniowy и Ryczałt.
    Проверяет наличие сценариев, допущений, анализа 120k и обязательного дисклеймера.
    """
    report = AgentDoradca.compare_tax_regimes(
        annual_revenue=Decimal('200000.00'),
        annual_costs=Decimal('40000.00'),
        ryczalt_rate=Decimal('0.12'),
        social_zus_annual=Decimal('4500.00')
    )

    # 1. Проверка структуры отчета
    assert len(report.scenarios) == 3
    assert len(report.assumptions) >= 1
    assert len(report.unknowns) >= 1
    assert "Nie stanowią one porady podatkowej" in report.disclaimer

    # 2. Проверка сценариев
    skala_sc = next(s for s in report.scenarios if "Skala" in s.regime_name)
    liniowy_sc = next(s for s in report.scenarios if "liniowy" in s.regime_name)
    ryczalt_sc = next(s for s in report.scenarios if "Ryczałt" in s.regime_name)

    assert skala_sc.gross_revenue == Decimal('200000.00')
    assert skala_sc.net_income_on_hand > Decimal('0.00')
    assert liniowy_sc.pit_due > Decimal('0.00')
    assert ryczalt_sc.pit_due > Decimal('0.00')

    # 3. Проверка анализа 120k zł (200k - 40k = 160k > 120k -> порог превышен)
    assert "przekraczasz I próg skali" in report.threshold_120k_analysis
