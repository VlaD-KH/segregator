"""
src/segregator/domain/models.py
Доменные модели и типизированные контракты данных для мультиагентной системы Segregator.
Все числовые финансовые значения строго типизированы через Decimal.
"""

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Dict, List, Optional, Union
from pydantic import BaseModel, Field, ConfigDict


# ==========================================
# 1. ИСТОЧНИКИ ДАННЫХ И ИЗВЛЕЧЕННЫЕ ПОЛЯ
# ==========================================

class DataSource(str, Enum):
    """Источники происхождения данных."""
    KSEF = "ksef"
    WHITE_LIST = "white_list"
    GUS = "gus"
    CEIDG = "ceidg"
    KRS = "krs"
    XML = "xml"
    TEXT = "text"
    OCR = "ocr"
    REGEX = "regex"
    LLM = "llm"
    HUMAN = "human"


class ExtractedField(BaseModel):
    """Поле с указанием источника и уровня достоверности."""
    model_config = ConfigDict(arbitrary_types_allowed=True)

    value: Optional[Union[str, Decimal, date, int, bool]] = None
    source: DataSource
    confidence: float = Field(ge=0.0, le=1.0, default=1.0)


class DocumentFacts(BaseModel):
    """
    Контракт на выходе Agent-01 (Agent Ingestion & Vision).
    Нормализованные первичные факты любого документа (KSeF XML или скан OCR).
    """
    doc_id: Optional[str] = None
    doc_type: str = Field(description="faktura | paragon | pit11 | wyciag | deklaracja | inne")
    ksef_reference_number: Optional[str] = None
    doc_number: Optional[ExtractedField] = None
    doc_date: Optional[ExtractedField] = None
    sale_date: Optional[ExtractedField] = None
    
    seller_nip: Optional[ExtractedField] = None
    seller_name: Optional[ExtractedField] = None
    buyer_nip: Optional[ExtractedField] = None
    buyer_name: Optional[ExtractedField] = None
    
    currency: str = "PLN"
    netto: Optional[ExtractedField] = None
    vat: Optional[ExtractedField] = None
    brutto: Optional[ExtractedField] = None
    vat_rates_breakdown: Dict[str, Decimal] = Field(default_factory=dict)
    
    is_split_payment_mpp: Optional[ExtractedField] = None
    iban: Optional[ExtractedField] = None
    
    decision: str = Field(default="ok", description="'ok' или 'escalate'")
    escalation_reason: Optional[str] = None
    raw_sha256: Optional[str] = None


# ==========================================
# 2. БУХГАЛТЕРСКАЯ КЛАССИФИКАЦИЯ (Agent-02)
# ==========================================

class BookingProposal(BaseModel):
    """
    Контракт на выходе Agent-02 (Agent Accounting & Classification).
    Бухгалтерская проводка, колонка KPiR и налоговая классификация.
    """
    category: str
    subcategory: Optional[str] = None
    kpir_column: Optional[int] = Field(None, description="Столбец KPiR (например: 7 - доход, 10 - товары, 13 - прочие расходы)")
    ryczalt_rate: Optional[Decimal] = Field(None, description="Ставка Ryczałt (например: 0.12 для 12%)")
    
    # Лимиты расходов на автотранспорт
    vehicle_usage_type: Optional[str] = Field(None, description="'mixed' (75% KUP) | 'business_only' (100% KUP) | 'private' (20% KUP)")
    kup_deductible_ratio: Decimal = Field(default=Decimal('1.00'), description="Доля расходов, признаваемых налоговыми (KUP)")
    vat_deductible_ratio: Decimal = Field(default=Decimal('1.00'), description="Доля вычета НДС (1.00 для 100%, 0.50 для 50%)")
    
    gtu_codes: List[str] = Field(default_factory=list, description="Коды GTU (GTU_01 .. GTU_13)")
    procedure_flags: List[str] = Field(default_factory=list, description="Процедуры (MPP, WNT, IMP, etc.)")
    basis: str = Field(default="rule", description="'rule' | 'precedent' | 'llm' | 'human'")
    confidence: float = 1.0


# ==========================================
# 3. ПРОФИЛЬ НАЛОГОПЛАТЕЛЬЩИКА И СТАТУСЫ
# ==========================================

class EmploymentType(str, Enum):
    """Типы трудовых и коммерческих отношений."""
    UOP = "Umowa o pracę"
    UZ = "Umowa zlecenie"
    UOD = "Umowa o dzieło"
    JDG = "JDG"
    ZARZAD = "Powołanie do Zarządu"


class EmploymentPeriod(BaseModel):
    """Период трудовой деятельности / бизнеса."""
    emp_type: EmploymentType
    start_date: date
    end_date: Optional[date] = None
    monthly_gross_avg: Decimal = Field(default=Decimal('0.00'))
    is_student_under_26: bool = False
    payer_nip: Optional[str] = None
    notes: Optional[str] = None


class TaxRegime(str, Enum):
    """Режимы налогообложения в Польше."""
    SKALA = "skala"              # 12% / 32% + kwota wolna 30 000 zł
    LINIOWY = "liniowy"          # 19%
    RYCZALT = "ryczalt"          # 2% - 17% от выручки
    CIT_ESTONSKI = "cit_estonski"# 0% до вывода дивидендов (Sp. z o.o.)
    CIT_KLASYCZNY = "cit_klasyczny" # 9% / 19%


class TaxpayerProfile(BaseModel):
    """Профиль налогоплательщика с историей изменения статусов."""
    pesel_masked: str
    nip: str
    full_name_masked: str = "Jan K*****"
    date_of_birth: date
    employment_history: List[EmploymentPeriod] = Field(default_factory=list)
    jdg_tax_regime: Optional[TaxRegime] = None
    is_vat_payer: bool = False
    mikrorachunek: Optional[str] = None
    zus_nrs_account: Optional[str] = None


# ==========================================
# 4. СТАТИСТИКА И ОБЯЗАТЕЛЬСТВА ZUS (Agent-03)
# ==========================================

class ZUSStage(str, Enum):
    """Стадии льгот по социальному страхованию для JDG."""
    ULGA_NA_START = "ulga_na_start"      # Первые 6 полных месяцев (только Zdrowotna, 0 соцвзносов)
    PREFERENCYJNY = "preferencyjny"      # Следующие 24 месяца (база 30% от минимальной зарплаты)
    MALY_ZUS_PLUS = "maly_zus_plus"      # До 36 месяцев в течение 60 (база от дохода прошлого года)
    DUZY_ZUS = "duzy_zus"                # Стандартный полный ZUS (база 60% от средней зарплаты)


class ZUSObligations(BaseModel):
    """Детализированный расчет обязательств ZUS за конкретный месяц."""
    month: str                           # Формат: 'YYYY-MM'
    stage: ZUSStage
    zbieg_tytulow: bool = False
    
    # Базы начисления
    spoleczne_base: Decimal = Decimal('0.00')
    zdrowotna_base: Decimal = Decimal('0.00')
    
    # Взносы социального страхования (Ubezpieczenia Społeczne)
    emerytalne: Decimal = Decimal('0.00')   # 19.52%
    rentowe: Decimal = Decimal('0.00')      # 8.00%
    chorobowe: Decimal = Decimal('0.00')    # 2.45% (добровольное для JDG)
    wypadkowe: Decimal = Decimal('0.00')    # 1.67% (стандарт)
    
    # Фонд труда и солидарности
    fundusz_pracy: Decimal = Decimal('0.00')# 2.45% (не платится на Ulga na start и Preferencyjny)
    
    # Медицинское страхование (Ubezpieczenie Zdrowotne)
    skladka_zdrowotna: Decimal = Decimal('0.00')
    
    # Итоговые суммы
    total_spoleczne: Decimal = Decimal('0.00')
    total_zus_do_zaplaty: Decimal = Decimal('0.00')
    
    forms_required: List[str] = Field(default_factory=list, description="['ZUS DRA', 'ZUS RCA', 'ZUS ZZA']")


# ==========================================
# 5. СОСТОЯНИЕ СИНХРОНИЗАЦИИ (Шаг +0)
# ==========================================

class SyncState(BaseModel):
    """Вектор состояния и водяные знаки для дифференциальной синхронизации."""
    nip: str
    ksef_last_sync_timestamp: Optional[datetime] = None
    ksef_last_reference_number: Optional[str] = None
    bank_last_booking_date: Optional[date] = None
    bank_last_tx_id: Optional[str] = None
    telegram_last_message_id: Optional[int] = None
    synced_sha256_hashes: List[str] = Field(default_factory=list)
