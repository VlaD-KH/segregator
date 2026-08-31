"""
src/segregator/orchestrator/state.py
Состояние мультиагентного графа Segregator (AccountingGraphState).
Передается между узлами конвейера: Step +0 -> Agent 01 -> Agent 02 -> Agent 03 -> Agent 04.
"""

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from src.segregator.domain.models import (
    BookingProposal,
    DocumentFacts,
    SyncState,
    TaxpayerProfile,
    ZUSObligations,
)
from src.segregator.accounting.kpir import KPiREntry
from src.segregator.tax.pit import MonthlyTaxResult, PayrollResult


class AuditEntry(BaseModel):
    """Запись в ślad rewizyjny (неизменяемый аудит-лог)."""
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    node_name: str
    action: str
    details: str
    confidence: float = 1.0


class AccountingGraphState(BaseModel):
    """
    Единое состояние мультиагентного графа Segregator.
    """
    # Входные параметры
    raw_input: Optional[str] = None          # Путь к файлу / XML / KSeF Reference Number
    input_type: str = "auto"                 # 'ksef_xml' | 'file_pdf' | 'file_img' | 'delta_sync'
    target_date: date = Field(default_factory=date.today)
    taxpayer_profile: Optional[TaxpayerProfile] = None
    
    # Шаг +0: Синхронизация состояний (Reconciler)
    sync_state: Optional[SyncState] = None
    sync_delta: List[str] = Field(default_factory=list) # Список новых неотработанных ID/хэшей
    is_delta_empty: bool = False
    
    # Agent-01: Ingestion & Vision
    facts: Optional[DocumentFacts] = None
    
    # Agent-02: Accounting & Classification
    proposal: Optional[BookingProposal] = None
    kpir_entry: Optional[KPiREntry] = None
    
    # Agent-03: Tax & Payroll
    zus_obligations: Optional[ZUSObligations] = None
    tax_result: Optional[MonthlyTaxResult] = None
    payroll_result: Optional[PayrollResult] = None
    
    # Human-in-the-Loop и маршрутизация
    status: str = "processing"               # 'processing' | 'escalated_to_human' | 'completed' | 'skipped_idle'
    escalation_reason: Optional[str] = None
    human_approved: bool = False
    human_notes: Optional[str] = None
    
    # Аудит и трассировка
    audit_trail: List[AuditEntry] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)
