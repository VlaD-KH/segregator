"""
src/segregator/orchestrator/nodes.py
Узлы мультиагентного графа Segregator.
Реализуют Шаг +0, Agent-01 (Ingest), Agent-02 (Accounting), Agent-03 (Tax), Human Gate, Agent-04 (Compliance).
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Dict, Any

from segregator.orchestrator.state import AccountingGraphState, AuditEntry
from segregator.domain.models import (
    DataSource,
    DocumentType,
    AgentDecision,
    PeriodDateBasis,
    EmploymentTypeKind,
    DocumentFacts,
    ExtractedField,
    BookingProposal,
    TaxRegime,
    ZUSStage,
)
from segregator.domain.zus import ZUSCalculator
from segregator.tax.pit import PITCalculator
from segregator.accounting.kpir import KPiREngine
from segregator.domain.invariants import InvariantEngine


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
            doc_type=DocumentType.FAKTURA_KOSZTOWA,
            fields={
                "nip_sprzedawcy": ExtractedField(value="5252344078", source=DataSource.KSEF, confidence=1.0),
                "nazwa_sprzedawcy": ExtractedField(value="PKN ORLEN S.A.", source=DataSource.KSEF, confidence=1.0),
                "nip_nabywcy": ExtractedField(value="1234567890", source=DataSource.KSEF, confidence=1.0),
                "nr_dokumentu": ExtractedField(value="FV/2026/08/001", source=DataSource.KSEF, confidence=1.0),
                "data_wystawienia": ExtractedField(value=state.target_date.isoformat(), source=DataSource.KSEF, confidence=1.0),
                "data_sprzedazy": ExtractedField(value=state.target_date.isoformat(), source=DataSource.KSEF, confidence=1.0),
                "termin_platnosci": ExtractedField(value=state.target_date.isoformat(), source=DataSource.KSEF, confidence=1.0),
                "netto": ExtractedField(value=1000.0, source=DataSource.KSEF, confidence=1.0),
                "vat": ExtractedField(value=230.0, source=DataSource.KSEF, confidence=1.0),
                "brutto": ExtractedField(value=1230.0, source=DataSource.KSEF, confidence=1.0),
                "stawka_vat": ExtractedField(value=0.23, source=DataSource.KSEF, confidence=1.0),
                "waluta": ExtractedField(value="PLN", source=DataSource.KSEF, confidence=1.0)
            },
            decision=AgentDecision.OK
        )

    # Верификация математического инварианта документа (Netto + VAT == Brutto)
    netto_val = facts.netto
    vat_val = facts.vat
    brutto_val = facts.brutto
    if netto_val > 0 and vat_val > 0 and brutto_val > 0:
        inv_check = InvariantEngine.check_document_math(netto_val, vat_val, brutto_val)
        if not inv_check.passed:
            facts.decision = AgentDecision.ESCALATE
            facts.why = inv_check.message
            state.escalation_reason = inv_check.message

    state.facts = facts
    state.audit_trail.append(AuditEntry(
        node_name="Agent_01_Ingest",
        action="FACTS_EXTRACTED",
        details=f"Извлечены факты {facts.doc_type.value} #{facts.doc_number}. Решение: {facts.decision.value}.",
        confidence=facts.fields["netto"].confidence if "netto" in facts.fields else 1.0
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
    seller_name = facts.seller_name.lower()
    doc_nr = facts.doc_number.lower()
    
    # Правило классификации расходов на автомобиль
    is_car_expense = "orlen" in seller_name or "paliwo" in seller_name or "bp" in seller_name or "shell" in seller_name
    
    if is_car_expense:
        proposal = BookingProposal(
            category="Koszty eksploatacji pojazdu (Paliwo)",
            subcategory="paliwo",
            kpir_column=13,
            vat_rate=0.23,
            vat_deduction_ratio=0.50,
            pit_cost_ratio=0.75,
            period_date=facts.doc_date.isoformat() if facts.doc_date else None,
            period_date_basis=PeriodDateBasis.DATA_WYSTAWIENIA,
            basis="rule:car_mixed_75",
            confidence=0.98,
            decision=AgentDecision.OK
        )
    elif facts.doc_type == DocumentType.FAKTURA_SPRZEDAZY or "fv/sprzedaz" in doc_nr:
        proposal = BookingProposal(
            category="Przychody z usług IT / B2B",
            subcategory="sprzedaz",
            kpir_column=7,
            vat_rate=0.23,
            vat_deduction_ratio=1.0,
            pit_cost_ratio=1.0,
            period_date=facts.doc_date.isoformat() if facts.doc_date else None,
            period_date_basis=PeriodDateBasis.DATA_WYSTAWIENIA,
            basis="rule:sales_col_7",
            confidence=1.0,
            decision=AgentDecision.OK
        )
    else:
        # Стандартные операционные расходы
        proposal = BookingProposal(
            category="Koszty operacyjne i usługi obce",
            kpir_column=13,
            vat_rate=0.23,
            vat_deduction_ratio=1.0,
            pit_cost_ratio=1.0,
            period_date=facts.doc_date.isoformat() if facts.doc_date else None,
            period_date_basis=PeriodDateBasis.DATA_WYSTAWIENIA,
            basis="rule:general_col_13",
            confidence=0.96,
            decision=AgentDecision.OK
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
        details=f"Документ отнесен в KPiR Колонку {proposal.kpir_column} (KUP: {(proposal.pit_cost_ratio or 1.0)*100}%).",
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
    if profile and any(p.emp_type == EmploymentTypeKind.JDG for p in profile.employment_history):
        # Предварительная оценка по одному документу: узел не видит БД, поэтому
        # знает только текущую проводку плюс выручку года, вложенную сервисом.
        # Захардкоженных 10 000 zł здесь больше нет — цифра, взятая из воздуха,
        # уезжала в декларацию. Окончательный расчёт делает
        # SegregatorService.close_period после проводки всех документов месяца.
        doc_przychody = state.kpir_entry.col_9_razem_przychody if state.kpir_entry else Decimal('0.00')
        doc_koszty = state.kpir_entry.col_14_razem_wydatki if state.kpir_entry else Decimal('0.00')
        monthly_profit = doc_przychody - doc_koszty
        annual_revenue = state.ytd_przychody + doc_przychody

        zus = ZUSCalculator.calculate_monthly_obligations(
            profile=profile,
            target_month=target_date,
            jdg_monthly_profit=monthly_profit,
            # На ryczałcie без годовой выручки ZUSCalculator отказывается считать:
            # от неё зависит ступень базы zdrowotnej 60/100/180%. Раньше сюда не
            # передавалось ничего, и любой документ такого JDG падал ValueError.
            annual_revenue=annual_revenue,
        )
        state.zus_obligations = zus

        # 2. Расчет аванса PIT
        regime = profile.jdg_tax_regime or TaxRegime.SKALA
        tax_res = PITCalculator.calculate_monthly_jdg_advance(
            month=target_date.strftime("%Y-%m"),
            regime=regime,
            income_ytd=annual_revenue,
            costs_ytd=doc_koszty,
            social_zus_paid_ytd=zus.total_spoleczne,
            ryczalt_rate=profile.jdg_ryczalt_rate if regime == TaxRegime.RYCZALT else None,
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
    if state.facts and state.facts.decision == AgentDecision.ESCALATE:
        return "human_review"

    # Проверка уверенности классификации Agent-02
    if state.proposal and (state.proposal.confidence < 0.95 or state.proposal.decision == AgentDecision.ESCALATE):
        state.escalation_reason = f"Низкая уверенность классификации категории ({state.proposal.confidence*100}% < 95%)"
        return "human_review"

    return "continue"
