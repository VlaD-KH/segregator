from datetime import date
from decimal import Decimal
from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field

# ==========================================
# 1. КОНТРАКТЫ АГЕНТОВ (JSON-СХЕМЫ)
# ==========================================

class DataSource(str, Enum):
    KSEF = "ksef"
    XML = "xml"
    TEXT = "text"
    OCR = "ocr"
    REGEX = "regex"
    LLM = "llm"
    HUMAN = "human"

class ExtractedField(BaseModel):
    value: str | Decimal | date | None
    source: DataSource
    confidence: float = Field(ge=0.0, le=1.0)

class DocumentFacts(BaseModel):
    """Контракт: Выход Agent-OCR-KSEF (Agent 01)"""
    doc_type: str
    nip_sprzedawcy: Optional[ExtractedField]
    nip_nabywcy: Optional[ExtractedField]
    doc_date: Optional[ExtractedField]
    netto: Optional[ExtractedField]
    vat: Optional[ExtractedField]
    brutto: Optional[ExtractedField]
    decision: str = Field(description="'ok' или 'escalate'")
    escalation_reason: Optional[str]

class BookingProposal(BaseModel):
    """Контракт: Выход Agent-Księgowy (Agent 02)"""
    category: str
    kpir_column: Optional[int] = Field(None, description="Столбец KPiR (например, 7, 10, 13)")
    ryczalt_rate: Optional[Decimal] = Field(None, description="Ставка Ryczałt, если применимо")
    vat_deduction_ratio: Optional[Decimal] = Field(None, description="1.0 (100%), 0.5 (50%) или 0.0")
    basis: str = Field(description="'precedent', 'rule' или 'llm'")

class PayrollFacts(BaseModel):
    """Контракт: Выход Agent-Kadrowy (Agent 03)"""
    zus_stage_by_month: dict[str, str] = Field(description="Формат 'YYYY-MM': 'ulga_na_start', 'preferencyjny', etc.")
    zbieg_tytulow: bool = Field(default=False)

# ==========================================
# 2. ПРОФИЛЬ НАЛОГОПЛАТЕЛЬЩИКА И СТАТУСЫ
# ==========================================

class EmploymentType(str, Enum):
    UOP = "Umowa o pracę"
    UZ = "Umowa zlecenie"
    JDG = "JDG"

class EmploymentPeriod(BaseModel):
    emp_type: EmploymentType
    start_date: date
    end_date: Optional[date]
    monthly_gross_avg: Decimal = Field(default=Decimal('0.00'))
    is_student_under_26: bool = Field(default=False)

class TaxpayerProfile(BaseModel):
    pesel_masked: str
    nip: str
    date_of_birth: date
    employment_history: List[EmploymentPeriod] = []
    jdg_tax_regime: Optional[str] = Field(description="'skala', 'liniowy', 'ryczalt'")

# ==========================================
# 3. ДЕТЕРМИНИРОВАННЫЙ КАЛЬКУЛЯТОР ZUS (Agent Tax)
# ==========================================

class ZUSStage(str, Enum):
    ULGA_NA_START = "ulga_na_start"
    PREFERENCYJNY = "preferencyjny"
    MALY_ZUS_PLUS = "maly_zus_plus"
    DUZY_ZUS = "duzy_zus"

class ZUSCalculator:
    """
    Детерминированный модуль расчета ZUS без использования LLM.
    Строго следует правилам: Ulga na start (6 мес) -> Preferencyjny (24 мес).
    Учитывает Zbieg tytułów ubezpieczeń.
    """
    
    MINIMAL_WAGE_2025 = Decimal('4626.00') # Пример значения, должно обновляться из БД/конфига
    
    @staticmethod
    def determine_zus_stage(jdg_start_date: date, target_date: date) -> ZUSStage:
        """Определяет стадию ZUS на основе хронологии."""
        if target_date < jdg_start_date:
            raise ValueError("Target date cannot be before JDG start date")
            
        months_active = (target_date.year - jdg_start_date.year) * 12 + (target_date.month - jdg_start_date.month)
        
        # Если деятельность начата не в первый день месяца, первый месяц не идет в счет 6 месяцев
        if jdg_start_date.day > 1:
            months_active -= 1
            
        if months_active < 6:
            return ZUSStage.ULGA_NA_START
        elif months_active < 30:
            return ZUSStage.PREFERENCYJNY
        else:
            return ZUSStage.DUZY_ZUS

    @staticmethod
    def check_zbieg_tytulow(profile: TaxpayerProfile, target_date: date) -> bool:
        """
        Проверяет пересечение титулов (Zbieg tytułów ubezpieczeń).
        Если в этом месяце есть активный UoP с ЗП >= минимальной, ИП освобождается от соц. взносов.
        """
        for emp in profile.employment_history:
            if emp.emp_type == EmploymentType.UOP:
                is_active = emp.start_date <= target_date and (emp.end_date is None or emp.end_date >= target_date)
                if is_active and emp.monthly_gross_avg >= ZUSCalculator.MINIMAL_WAGE_2025:
                    return True
        return False

    @staticmethod
    def calculate_monthly_obligations(profile: TaxpayerProfile, target_month: date) -> dict:
        """Возвращает флаги обязательств для конкретного месяца."""
        jdg_period = next((p for p in profile.employment_history if p.emp_type == EmploymentType.JDG), None)
        
        if not jdg_period or target_month < jdg_period.start_date:
            return {"status": "No active JDG"}
            
        stage = ZUSCalculator.determine_zus_stage(jdg_period.start_date, target_month)
        has_zbieg = ZUSCalculator.check_zbieg_tytulow(profile, target_month)
        
        # Основная логика: платим ли социальные взносы?
        pays_social = False if has_zbieg or stage == ZUSStage.ULGA_NA_START else True
        
        return {
            "month": target_month.strftime("%Y-%m"),
            "zus_stage": stage.value,
            "zbieg_tytulow_active": has_zbieg,
            "obligations": {
                "skladka_zdrowotna": True,  # Платится всегда
                "skladki_spoleczne": pays_social,
                "fundusz_pracy": pays_social and stage != ZUSStage.PREFERENCYJNY # На преференции FP не платится
            }
        }