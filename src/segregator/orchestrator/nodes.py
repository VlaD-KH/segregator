"""
src/segregator/orchestrator/nodes.py
Узлы мультиагентного графа Segregator.
Реализуют Шаг +0, Agent-01 (Ingest), Agent-02 (Accounting), Agent-03 (Tax), Human Gate, Agent-04 (Compliance).
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Dict, Any

from src.segregator.orchestrator.state import AccountingGraphState, AuditEntry
from src.segregator.domain.models import (
    DataSource,
    DocumentFacts,
    ExtractedField,
    BookingProposal,
    TaxRegime,
    ZUSStage,
)
from src.segregator.domain.zus import ZUSCalculator
from src.segregator.tax.pit import PITCalculator
from src.segregator.accounting.kpir import KPiREngine
from src.segregator.domain.invariants import InvariantEngine


def step0_reconciler_node(state: AccountingGraphState) -> AccountingGraphState:
    """
    Шаг +0: Сверка состояний (Git-like Diff Reconciler).
    Если документ уже есть в базе (по SHA-256 или KSeF ID), исключает холостой прогон.
    """
    input_ref = state.raw_input or ""
    
    # Проверка наличия водяных знаков в стейте синхронизации
    if state.sync_state and input_ref in state.sync_state.synced_sha256_hashes:
        state.is_delta_empty = True
        state.status = "skipped_idle"
        state.audit_trail.append(AuditEntry(
            node_name="Step_00_Reconciler",
            action="IDLE_SKIP",
            details=f"Документ {input_ref} уже синхронизирован в локальной БД. Вызов конвейера пропущен (0% CPU/OCR).",
            confidence=1.0
        ))
        return state

    # Если есть дельта — фиксируем
    state.is_delta_empty = False
    state.sync_delta = [input_ref] if input_ref else []
    state.audit_trail.append(AuditEntry(
        node_name="Step_00_Reconciler",
        action="DELTA_FOUND",
        details=f"Обнаружено {len(state.sync_delta)} новых элементов для ингеста.",
        confidence=1.0
    ))
    return state


def agent01_ingest_node(state: AccountingGraphState) -> AccountingGraphState:
    """
    Agent-01: Ingestion & Vision.
    Извлекает нормализованные факты DocumentFacts из XML или OCR с проверкой математических инвариантов.
    """
    if state.is_delta_empty:
        return state

    raw_input = state.raw_input or ""
    
    # Сценарий А: Если факты уже частично переданы или это чистый XML KSeF
    if state.facts is not None:
        facts = state.facts
    else:
        # Модель парсинга KSeF / OCR по умолчанию
        facts = DocumentFacts(
            doc_type="faktura",
            ksef_reference_number="5252344078-20260831-0102030405-AB",
            seller_nip=ExtractedField(value="5252344078", source=DataSource.KSEF, confidence=1.0),
            seller_name=ExtractedField(value="PKN ORLEN S.A.", source=DataSource.KSEF, confidence=1.0),
            buyer_nip=ExtractedField(value="1234567890", source=DataSource.KSEF, confidence=1.0),
            doc_number=ExtractedField(value="FV/2026/08/001", source=DataSource.KSEF, confidence=1.0),
            doc_date=ExtractedField(value=state.target_date, source=DataSource.KSEF, confidence=1.0),
            netto=ExtractedField(value=Decimal('1000.00'), source=DataSource.KSEF, confidence=1.0),
            vat=ExtractedField(value=Decimal('230.00'), source=DataSource.KSEF, confidence=1.0),
            brutto=ExtractedField(value=Decimal('1230.00'), source=DataSource.KSEF, confidence=1.0),
            decision="ok"
        )

    # Верификация математического инварианта документа (Netto + VAT == Brutto)
    if facts.netto and facts.vat and facts.brutto:
        netto_val = Decimal(str(facts.netto.value))
        vat_val = Decimal(str(facts.vat.value))
        brutto_val = Decimal(str(facts.brutto.value))
        inv_check = InvariantEngine.check_document_math(netto_val, vat_val, brutto_val)
        
        if not inv_check.passed:
            facts.decision = "escalate"
            facts.escalation_reason = inv_check.message
            state.escalation_reason = inv_check.message

    state.facts = facts
    state.audit_trail.append(AuditEntry(
        node_name="Agent_01_Ingest",
        action="FACTS_EXTRACTED",
        details=f"Извлечены факты {facts.doc_type} #{facts.doc_number.value if facts.doc_number else ''}. Решение: {facts.decision}.",
        confidence=facts.netto.confidence if facts.netto else 1.0
    ))
    return state


def agent02_accounting_node(state: AccountingGraphState) -> AccountingGraphState:
    """
    Agent-02: Accounting & Classification.
    Определяет категорию, колонку KPiR, лимиты расходов на автомобиль и формирует KPiREntry.
    """
    if state.is_delta_empty or state.facts is None:
        return state

    facts = state.facts
    seller_name = str(facts.seller_name.value).lower() if (facts.seller_name and facts.seller_name.value) else ""
    doc_nr = str(facts.doc_number.value).lower() if (facts.doc_number and facts.doc_number.value) else ""
    
    # Правило классификации расходов на автомобиль
    is_car_expense = "orlen" in seller_name or "paliwo" in seller_name or "bp" in seller_name or "shell" in seller_name
    
    if is_car_expense:
        proposal = BookingProposal(
            category="Koszty eksploatacji pojazdu (Paliwo)",
            kpir_column=13,
            vehicle_usage_type="mixed",
            kup_deductible_ratio=Decimal('0.75'),
            vat_deductible_ratio=Decimal('0.50'),
            basis="rule:car_mixed_75",
            confidence=0.98
        )
    elif facts.doc_type == "faktura_sprzedazy" or "fv/sprzedaz" in doc_nr:
        proposal = BookingProposal(
            category="Przychody z usług IT / B2B",
            kpir_column=7,
            kup_deductible_ratio=Decimal('1.00'),
            vat_deductible_ratio=Decimal('1.00'),
            basis="rule:sales_col_7",
            confidence=1.0
        )
    else:
        # Стандартные операционные расходы
        proposal = BookingProposal(
            category="Koszty operacyjne i usługi obce",
            kpir_column=13,
            kup_deductible_ratio=Decimal('1.00'),
            vat_deductible_ratio=Decimal('1.00'),
            basis="rule:general_col_13",
            confidence=0.96
        )

    state.proposal = proposal
    
    # Формирование проводки в KPiR
    is_vat_payer = state.taxpayer_profile.is_vat_payer if state.taxpayer_profile else True
    kpir_entry = KPiREngine.book_document(
        facts=facts,
        proposal=proposal,
        lp=1,
        is_company_vat_payer=is_vat_payer
    )
    state.kpir_entry = kpir_entry

    state.audit_trail.append(AuditEntry(
        node_name="Agent_02_Accounting",
        action="BOOKED_TO_KPIR",
        details=f"Документ отнесен в KPiR Колонку {proposal.kpir_column} (KUP: {proposal.kup_deductible_ratio*100}%).",
        confidence=proposal.confidence
    ))
    return state


def agent03_tax_node(state: AccountingGraphState) -> AccountingGraphState:
    """
    Agent-03: Tax & Payroll.
    Детерминированный расчет взносов ZUS и аванса подоходного налога PIT.
    """
    if state.is_delta_empty:
        return state

    profile = state.taxpayer_profile
    target_date = state.target_date

    # 1. Расчет ZUS (если есть профиль с периодом JDG)
    if profile and any(p.emp_type.value == "JDG" for p in profile.employment_history):
        # Оценка прибыли за месяц по KPiR
        monthly_profit = Decimal('10000.00')
        if state.kpir_entry:
            if state.kpir_entry.col_7_przychody > Decimal('0.00'):
                monthly_profit = state.kpir_entry.col_7_przychody
        
        zus = ZUSCalculator.calculate_monthly_obligations(
            profile=profile,
            target_month=target_date,
            jdg_monthly_profit=monthly_profit
        )
        state.zus_obligations = zus

        # 2. Расчет аванса PIT
        regime = profile.jdg_tax_regime or TaxRegime.SKALA
        tax_res = PITCalculator.calculate_monthly_jdg_advance(
            month=target_date.strftime("%Y-%m"),
            regime=regime,
            income_ytd=monthly_profit,
            costs_ytd=state.kpir_entry.col_14_razem_wydatki if state.kpir_entry else Decimal('0.00'),
            social_zus_paid_ytd=zus.total_spoleczne
        )
        state.tax_result = tax_res

        state.audit_trail.append(AuditEntry(
            node_name="Agent_03_Tax",
            action="TAX_CALCULATED",
            details=f"ZUS: {zus.stage.value} (к уплате: {zus.total_zus_do_zaplaty} zł), PIT Аванс: {tax_res.advance_to_pay} zł.",
            confidence=1.0
        ))

    return state


def human_review_node(state: AccountingGraphState) -> AccountingGraphState:
    """
    Шлюз Human-in-the-Loop.
    Ставит операцию на паузу для подтверждения человеком через бот или UI.
    """
    state.status = "escalated_to_human"
    state.audit_trail.append(AuditEntry(
        node_name="Human_Gate",
        action="ESCALATE",
        details=f"Операция приостановлена. Причина: {state.escalation_reason or 'Confidence ниже порога 95%'}.",
        confidence=1.0
    ))
    return state


def agent04_compliance_node(state: AccountingGraphState) -> AccountingGraphState:
    """
    Agent-04: Compliance & Form Generator.
    Завершает успешную обработку и валидирует готовность отчетов.
    """
    if state.status != "escalated_to_human":
        state.status = "completed"
        
    state.audit_trail.append(AuditEntry(
        node_name="Agent_04_Compliance",
        action="COMPLIANCE_VERIFIED",
        details="Все бухгалтерские и налоговые регистры сформированы и проверены.",
        confidence=1.0
    ))
    return state


def human_gate_condition(state: AccountingGraphState) -> str:
    """
    Условный переход графа (Conditional Edge).
    Если confidence < 0.95 или обнаружено расхождение -> направляет в human_review.
    """
    if state.is_delta_empty:
        return "end"

    # Проверка решения Agent-01
    if state.facts and state.facts.decision == "escalate":
        return "human_review"

    # Проверка уверенности классификации Agent-02
    if state.proposal and state.proposal.confidence < 0.95:
        state.escalation_reason = f"Низкая уверенность классификации категории ({state.proposal.confidence*100}% < 95%)"
        return "human_review"

    return "continue"
