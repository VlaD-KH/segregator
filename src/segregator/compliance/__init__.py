"""
segregator.compliance package exports.
"""

from segregator.compliance.pit36 import (
    IncomeSourceRecord,
    PITBAttachment,
    PIT36Declaration,
    PIT36Consolidator,
)
from segregator.compliance.jpk_v7 import (
    JPKSalesRecord,
    JPKPurchaseRecord,
    JPKV7MGenerator,
)
from segregator.compliance.zus_kedu import (
    ZUSKEDUGenerator,
)
from segregator.compliance.xml_validator import (
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
