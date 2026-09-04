"""
segregator.domain package exports.
"""

from segregator.domain.models import (
    DocumentType,
    DataSource,
    AgentDecision,
    PeriodDateBasis,
    EmploymentTypeKind,
    PayrollSource,
    ZUSStage,
    TaxRegime,
    ExtractedField,
    DocumentFacts,
    BookingProposal,
    PayrollPeriodItem,
    PayrollFacts,
    AdvisoryScenarioItem,
    AdvisoryReport,
    EmploymentPeriod,
    TaxpayerProfile,
    ZUSObligations,
    SyncState,
    DISCLAIMER_PL,
    mask_iban,
    mask_sensitive_fields,
    is_sensitive_field_name,
)
from segregator.domain.zus import ZUSCalculator, ZUSConstants
from segregator.domain.invariants import InvariantEngine

__all__ = [
    "DocumentType",
    "DataSource",
    "AgentDecision",
    "PeriodDateBasis",
    "EmploymentTypeKind",
    "EmploymentType",
    "PayrollSource",
    "ZUSStage",
    "TaxRegime",
    "ExtractedField",
    "DocumentFacts",
    "BookingProposal",
    "PayrollPeriodItem",
    "PayrollFacts",
    "AdvisoryScenarioItem",
    "AdvisoryReport",
    "EmploymentPeriod",
    "TaxpayerProfile",
    "ZUSObligations",
    "SyncState",
    "DISCLAIMER_PL",
    "mask_iban",
    "mask_sensitive_fields",
    "is_sensitive_field_name",
    "ZUSCalculator",
    "ZUSConstants",
    "InvariantEngine",
]

# Совместимость: на main это имя экспортировалось из пакета. Ветка его
# переименовала, и молчаливое удаление публичного имени из __init__ ничем
# не отмечено в диффе.
EmploymentType = EmploymentTypeKind
