"""
tests/test_advisor_doradca.py
Тесты для советника Agent-Doradca (Agent 04) и детерминированных симуляций налоговых режимов.
"""

from decimal import Decimal
import pytest
from pydantic import ValidationError

from segregator.advisor.doradca import AgentDoradca
from segregator.domain.models import DISCLAIMER_PL, AdvisoryReport, AdvisoryScenarioItem


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
    # Полная фраза, а не подстрока из двух слов: «doradztwo podatkowe» встречается
    # и в предложении, которое утверждает обратное.
    assert report.disclaimer == DISCLAIMER_PL

    # 2. Проверка сценариев
    skala_sc = next(s for s in report.scenarios if "Skala" in s.name)
    liniowy_sc = next(s for s in report.scenarios if "liniowy" in s.name)
    ryczalt_sc = next(s for s in report.scenarios if "Ryczałt" in s.name)

    assert skala_sc.figures["przychod"] == 200000.00
    assert skala_sc.figures["zysk_netto"] > 0.00
    assert liniowy_sc.figures["podatek_pit"] > 0.00
    assert ryczalt_sc.figures["podatek_pit"] > 0.00

    # 3. Проверка анализа 120k zł (200k - 40k = 160k > 120k -> порог превышен)
    assert "Przekroczono próg 120 000 zł" in report.note


# ---------------------------------------------------------------------------
# Обязательный дисклеймер: юридическая граница держится валидатором
# ---------------------------------------------------------------------------

def _minimal_scenarios():
    return [
        AdvisoryScenarioItem(name="A", figures={"przychod": 1.0}, tradeoff="t"),
        AdvisoryScenarioItem(name="B", figures={"przychod": 2.0}, tradeoff="t"),
    ]


def test_disclaimer_cannot_be_empty():
    """Пустой дисклеймер должен отвергаться моделью, а не проходить насквозь."""
    with pytest.raises(ValidationError):
        AdvisoryReport(scenarios=_minimal_scenarios(), assumptions=["a"], disclaimer="")


def test_disclaimer_must_contain_the_negation():
    """Фраза без отрицания — не дисклеймер, даже если слова те же.

    «To jest doradztwo podatkowe» содержит «doradztwo podatkowe», но утверждает
    ровно обратное тому, что требует юридическая граница.
    """
    with pytest.raises(ValidationError):
        AdvisoryReport(
            scenarios=_minimal_scenarios(),
            assumptions=["a"],
            disclaimer="To jest doradztwo podatkowe.",
        )


def test_disclaimer_default_is_the_canonical_phrase():
    report = AdvisoryReport(scenarios=_minimal_scenarios(), assumptions=["a"])
    assert report.disclaimer == DISCLAIMER_PL


# ---------------------------------------------------------------------------
# Ryczałt: składka zdrowotna по ступеням, отказ на годе без таблиц
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "annual_revenue, expected_zdrowotna_annual",
    [
        (Decimal('50000.00'), 5539.92),    # <= 60k  -> 60%  ступень
        (Decimal('200000.00'), 9233.16),   # <= 300k -> 100% ступень
        (Decimal('400000.00'), 16619.64),  # > 300k  -> 180% ступень
    ],
)
def test_ryczalt_health_contribution_follows_revenue_tier(annual_revenue, expected_zdrowotna_annual):
    """Годовая zdrowotna на ryczałcie зависит от przychodu, а не константа 8760.00."""
    report = AgentDoradca.compare_tax_regimes(
        annual_revenue=annual_revenue,
        annual_costs=Decimal('0.00'),
        social_zus_annual=Decimal('0.00'),
        year=2025,
    )
    ryczalt_sc = next(s for s in report.scenarios if "Ryczałt" in s.name)
    assert ryczalt_sc.figures["skladki_zus"] == pytest.approx(expected_zdrowotna_annual, abs=0.01)


def test_advisor_refuses_year_without_tables():
    """Год без нормативных таблиц -> отказ считать, а не молчаливый счёт по 2025."""
    with pytest.raises(ValueError, match="Odmowa kalkulacji"):
        AgentDoradca.compare_tax_regimes(
            annual_revenue=Decimal('200000.00'),
            annual_costs=Decimal('40000.00'),
            year=2030,
        )
