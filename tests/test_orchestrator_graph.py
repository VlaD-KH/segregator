"""
tests/test_orchestrator_graph.py
Модульные тесты для мультиагентного графа Segregator (StateGraph / Orchestrator).
"""

from datetime import date
from decimal import Decimal
import pytest

from src.segregator.domain.models import (
    DataSource,
    DocumentFacts,
    ExtractedField,
    BookingProposal,
    TaxpayerProfile,
    EmploymentPeriod,
    EmploymentType,
    TaxRegime,
    SyncState,
)
from src.segregator.orchestrator.state import AccountingGraphState
from src.segregator.orchestrator.graph import build_accounting_graph


@pytest.fixture
def sample_taxpayer_profile():
    """Тестовый профиль ИП на Skala podatkowa."""
    return TaxpayerProfile(
        pesel_masked="900101*****",
        nip="5252344078",
        date_of_birth=date(1990, 1, 1),
        jdg_tax_regime=TaxRegime.SKALA,
        employment_history=[
            EmploymentPeriod(
                emp_type=EmploymentType.JDG,
                start_date=date(2025, 10, 1)
            )
        ]
    )


def test_graph_happy_path(sample_taxpayer_profile):
    """
    Тест сквозного прохождения документа через мультиагентный граф:
    Step +0 -> Agent 01 -> Agent 02 -> Agent 03 -> Agent 04 -> Completed.
    """
    graph = build_accounting_graph()
    
    initial_state = AccountingGraphState(
        raw_input="FV/2026/08/001_HASH123",
        target_date=date(2025, 11, 1),
        taxpayer_profile=sample_taxpayer_profile
    )
    
    final_state = graph.invoke(initial_state)
    
    # 1. Проверка статуса
    assert final_state.status == "completed"
    assert final_state.is_delta_empty is False
    
    # 2. Проверка фактов Agent-01
    assert final_state.facts is not None
    assert final_state.facts.seller_nip.value == "5252344078"
    assert final_state.facts.netto.value == Decimal('1000.00')
    
    # 3. Проверка классификации Agent-02 (Orlen -> Mixed car -> 75% KUP)
    assert final_state.proposal is not None
    assert final_state.proposal.kpir_column == 13
    assert final_state.proposal.kup_deductible_ratio == Decimal('0.75')
    assert final_state.kpir_entry is not None
    assert final_state.kpir_entry.col_13_pozostale_wydatki == Decimal('836.25')
    
    # 4. Проверка налогов Agent-03 (Ulga na start)
    assert final_state.zus_obligations is not None
    assert final_state.zus_obligations.total_spoleczne == Decimal('0.00')
    assert final_state.tax_result is not None
    
    # 5. Проверка следа аудита
    assert len(final_state.audit_trail) >= 5


def test_graph_step0_zero_idle_waste(sample_taxpayer_profile):
    """
    Тест Шага +0: Исключение холостого прогона.
    Если документ уже есть в водяных знаках базы -> мгновенный выход со статусом skipped_idle.
    """
    graph = build_accounting_graph()
    
    known_hash = "ALREADY_SYNCED_HASH_999"
    sync_state = SyncState(
        nip="5252344078",
        synced_sha256_hashes=[known_hash]
    )
    
    initial_state = AccountingGraphState(
        raw_input=known_hash,
        sync_state=sync_state,
        target_date=date(2025, 11, 1),
        taxpayer_profile=sample_taxpayer_profile
    )
    
    final_state = graph.invoke(initial_state)
    
    assert final_state.status == "skipped_idle"
    assert final_state.is_delta_empty is True
    assert final_state.facts is None # Agent-01 даже не вызывался!
    assert final_state.kpir_entry is None


def test_graph_human_in_the_loop_on_math_discrepancy(sample_taxpayer_profile):
    """
    Тест шлюза Human-in-the-Loop:
    При нарушении арифметического инварианта фактуры -> эскалация человеку.
    """
    graph = build_accounting_graph()
    
    # Фактура с математической ошибкой: Netto 1000 + VAT 230 != Brutto 1500
    invalid_facts = DocumentFacts(
        doc_type="faktura",
        seller_nip=ExtractedField(value="5252344078", source=DataSource.OCR, confidence=0.90),
        netto=ExtractedField(value=Decimal('1000.00'), source=DataSource.OCR),
        vat=ExtractedField(value=Decimal('230.00'), source=DataSource.OCR),
        brutto=ExtractedField(value=Decimal('1500.00'), source=DataSource.OCR), # Ошибка!
        decision="ok"
    )
    
    initial_state = AccountingGraphState(
        raw_input="INVALID_DOC_SCAN_404",
        facts=invalid_facts,
        target_date=date(2025, 11, 1),
        taxpayer_profile=sample_taxpayer_profile
    )
    
    final_state = graph.invoke(initial_state)
    
    assert final_state.status == "escalated_to_human"
    assert final_state.escalation_reason is not None
    assert "Расхождение в сумме фактуры" in final_state.escalation_reason
    assert any(a.node_name == "Human_Gate" for a in final_state.audit_trail)
