"""
src/segregator/compliance package exports.
"""

from src.segregator.compliance.pit36 import (
    IncomeSourceRecord,
    PITBAttachment,
    PIT36Declaration,
    PIT36Consolidator,
)
from src.segregator.compliance.jpk_v7 import (
    JPKSalesRecord,
    JPKPurchaseRecord,
    JPKV7MGenerator,
)
from src.segregator.compliance.zus_kedu import (
    ZUSKEDUGenerator,
)
from src.segregator.compliance.xml_validator import (
    ValidationResult,
    ComplianceXMLValidator,
)

__all__ = [
    "IncomeSourceRecord",
    "PITBAttachment",
    "PIT36Declaration",
    "PIT36Consolidator",
    "JPKSalesRecord",
    "JPKPurchaseRecord",
    "JPKV7MGenerator",
    "ZUSKEDUGenerator",
    "ValidationResult",
    "ComplianceXMLValidator",
]
