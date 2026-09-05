"""
src/segregator/domain/models.py
Строгие доменные модели и контракты мультиагентной системы Segregator (Польша).
100% совместимость с JSON-схемами в agents/schemas/*.json (draft 2020-12).
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel, ConfigDict, Field, field_validator


# =========================================================================
# ENUMS & CONSTANTS
# =========================================================================

class DocumentType(str, Enum):
    """Строгий enum типов документов согласно agents/schemas/document_facts.json."""
    FAKTURA_SPRZEDAZY = "faktura_sprzedazy"
    FAKTURA_KOSZTOWA = "faktura_kosztowa"
    FAKTURA_KORYGUJACA = "faktura_korygujaca"
    PARAGON = "paragon"
    RACHUNEK = "rachunek"
    DEKLARACJA_ZUS = "deklaracja_zus"
    DECYZJA_ZUS = "decyzja_zus"
    PIT_11 = "pit_11"
    UMOWA = "umowa"
    WYCIAG_BANKOWY = "wyciag_bankowy"
    POTWIERDZENIE_PRZELEWU = "potwierdzenie_przelewu"
    INNE = "inne"


class DataSource(str, Enum):
    """Источники извлечения полей согласно схеме."""
    KSEF = "ksef"
    XML = "xml"
    TEXT = "text"
    OCR = "ocr"
    REGEX = "regex"
    LLM = "llm"
    HUMAN = "human"


class AgentDecision(str, Enum):
    """Решение агента по документу."""
    OK = "ok"
    ESCALATE = "escalate"


class PeriodDateBasis(str, Enum):
    """Основание даты отнесения документа в реестр."""
    DATA_WYSTAWIENIA = "data_wystawienia"
    DATA_SPRZEDAZY = "data_sprzedazy"
    DATA_PLATNOSCI = "data_platnosci"
    DATA_WIADOMOSCI = "data_wiadomosci"


class EmploymentTypeKind(str, Enum):
    """Коды типов занятости согласно agents/schemas/payroll_facts.json."""
    UOP = "uop"
    UZ = "uz"
    JDG = "jdg"
    BRAK = "brak"


class PayrollSource(str, Enum):
    """Источник данных о занятости."""
    PIT_11 = "pit_11"
    PROFILE = "profile"
    HUMAN = "human"


class ZUSStage(str, Enum):
    """Стадии льгот ZUS согласно agents/schemas/payroll_facts.json."""
    ULGA_NA_START = "ulga_na_start"
    PREFERENCYJNY = "preferencyjny"
    MALY_ZUS_PLUS = "maly_zus_plus"
    DUZY_ZUS = "duzy_zus"
    BRAK = "brak"


class TaxRegime(str, Enum):
    """Налоговые режимы Польши."""
    SKALA = "skala"
    LINIOWY = "liniowy"
    RYCZALT = "ryczalt"
    CIT_ESTONSKI = "cit_estonski"


# Обязательный дисклеймер отчёта советника. Юридическая граница проекта:
# система готовит расчёт для бухгалтера, а не даёт налоговую консультацию
# (CLAUDE.md «Юридическая граница», agents/schemas/advisory_report.json).
# Один источник строки на модель, схему и тест.
DISCLAIMER_PL = (
    "To jest kalkulacja, a nie doradztwo podatkowe. "
    "Wszelkie decyzje podejmuje przedsiębiorca."
)
def _normalise_disclaimer(text: str) -> str:
    """Схлопнуть пробелы и регистр — сравнение не должно падать из-за вёрстки."""
    return re.sub(r"\s+", " ", text or "").strip().casefold()


# Маскирование переехало в `domain/masking.py`: оно зонируется как R, и туда
# должен уезжать один файл, а не весь модуль моделей. Имена оставлены здесь
# импортом — на них ссылаются service.py, probe/ и тесты.
#
# NB: `logging.py` держит собственный набор SENSITIVE_KEYS с тем же изъяном
# (точное совпадение по «iban»), но это зона R — сведение двух списков в один
# делается отдельным заходом с одобрения владельца.
from segregator.domain.masking import (  # noqa: F401  (реэкспорт)
    is_sensitive_field_name,
    mask_iban,
    mask_ibans_in_text,
    mask_sensitive_fields,
    validate_iban_mod97,
)


# =========================================================================
# 1. DOCUMENT FACTS (Agent 01 -> agents/schemas/document_facts.json)
# =========================================================================

class ExtractedField(BaseModel):
    """Единичное извлеченное поле документа."""
    model_config = ConfigDict(extra="forbid")

    value: Optional[Union[str, float, int]] = None
    source: DataSource
    confidence: float = Field(ge=0.0, le=1.0)


class DocumentFacts(BaseModel):
    """
    Факты документа, извлеченные Agent-01 (OCR/KSeF).
    Строго валидируется по agents/schemas/document_facts.json.
    """
    model_config = ConfigDict(extra="forbid")

    doc_type: DocumentType
    fields: Dict[str, ExtractedField] = Field(default_factory=dict)
    decision: AgentDecision = AgentDecision.OK
    why: Optional[str] = Field(default=None, max_length=300)

    # Удобные типизированные свойства-аксессоры для детерминированных калькуляторов
    def get_field_val(self, key: str) -> Optional[Any]:
        f = self.fields.get(key)
        return f.value if f else None

    @property
    def netto(self) -> Decimal:
        v = self.get_field_val("netto")
        return Decimal(str(v)) if v is not None else Decimal('0.00')

    @property
    def vat(self) -> Decimal:
        v = self.get_field_val("vat")
        return Decimal(str(v)) if v is not None else Decimal('0.00')

    @property
    def brutto(self) -> Decimal:
        v = self.get_field_val("brutto")
        return Decimal(str(v)) if v is not None else Decimal('0.00')

    @property
    def seller_nip(self) -> str:
        v = self.get_field_val("nip_sprzedawcy")
        return str(v) if v is not None else ""

    @property
    def buyer_nip(self) -> str:
        v = self.get_field_val("nip_nabywcy")
        return str(v) if v is not None else ""

    @property
    def seller_name(self) -> str:
        v = self.get_field_val("nazwa_sprzedawcy")
        return str(v) if v is not None else ""

    @property
    def doc_number(self) -> str:
        v = self.get_field_val("nr_dokumentu")
        return str(v) if v is not None else ""

    @property
    def doc_date(self) -> Optional[date]:
        v = self.get_field_val("data_wystawienia")
        if isinstance(v, date):
            return v
        if isinstance(v, str):
            try:
                return date.fromisoformat(v)
            except ValueError:
                pass
        return None

    @property
    def currency(self) -> str:
        v = self.get_field_val("waluta")
        return str(v) if v else "PLN"


# =========================================================================
# 2. BOOKING PROPOSAL (Agent 02 -> agents/schemas/booking_proposal.json)
# =========================================================================

class BookingProposal(BaseModel):
    """
    Бухгалтерское предложение проводки от Agent-02 (Księgowy).
    Строго валидируется по agents/schemas/booking_proposal.json.
    """
    model_config = ConfigDict(extra="forbid")

    category: str
    subcategory: Optional[str] = None
    kpir_column: Optional[int] = Field(default=None, ge=1, le=16)
    account: Optional[str] = None
    vat_rate: Optional[float] = None
    vat_deduction_ratio: Optional[float] = None # 0, 0.5, 1, None
    pit_cost_ratio: Optional[float] = None      # 0, 0.75, 1, None
    period_date: Optional[str] = None           # ISO-8601 YYYY-MM-DD
    period_date_basis: Optional[PeriodDateBasis] = None
    confidence: float = Field(ge=0.0, le=1.0)
    basis: str                                  # precedent:<id> | rule:<id> | llm
    decision: AgentDecision = AgentDecision.OK
    why: Optional[str] = Field(default=None, max_length=300)

    @field_validator("vat_deduction_ratio")
    @classmethod
    def validate_vat_ratio(cls, v: Optional[float]) -> Optional[float]:
        if v is not None and v not in (0.0, 0.5, 1.0):
            raise ValueError("vat_deduction_ratio must be 0, 0.5, 1 or None")
        return v

    @field_validator("pit_cost_ratio")
    @classmethod
    def validate_pit_ratio(cls, v: Optional[float]) -> Optional[float]:
        if v is not None and v not in (0.0, 0.75, 1.0):
            raise ValueError("pit_cost_ratio must be 0, 0.75, 1 or None")
        return v


# =========================================================================
# 3. PAYROLL FACTS (Agent 03 -> agents/schemas/payroll_facts.json)
# =========================================================================

class PayrollPeriodItem(BaseModel):
    """Период занятости согласно agents/schemas/payroll_facts.json."""
    model_config = ConfigDict(extra="forbid")

    from_date: str = Field(alias="from")
    to_date: str = Field(alias="to")
    kind: EmploymentTypeKind
    payer_nip: Optional[str] = None
    gross: Optional[float] = None
    kup: Optional[float] = None
    advance_withheld: Optional[float] = None
    source: PayrollSource


class PayrollFacts(BaseModel):
    """
    Кадровые факты года от Agent-03 (Kadrowy).
    Строго валидируется по agents/schemas/payroll_facts.json.
    """
    model_config = ConfigDict(extra="forbid")

    periods: List[PayrollPeriodItem] = Field(default_factory=list)
    zus_stage_by_month: Dict[str, ZUSStage] = Field(default_factory=dict)
    zbieg_tytulow: Optional[bool] = None
    decision: AgentDecision = AgentDecision.OK
    why: Optional[str] = Field(default=None, max_length=300)


# =========================================================================
# 4. ADVISORY REPORT (Agent 04 -> agents/schemas/advisory_report.json)
# =========================================================================

class AdvisoryScenarioItem(BaseModel):
    """Сценарий в отчете советника согласно agents/schemas/advisory_report.json."""
    model_config = ConfigDict(extra="forbid")

    name: str
    figures: Dict[str, float]
    effective_burden_pct: Optional[float] = None
    tradeoff: str


class AdvisoryReport(BaseModel):
    """
    Отчет Agent-Doradca (Agent 04).
    Строго валидируется по agents/schemas/advisory_report.json.
    """
    model_config = ConfigDict(extra="forbid")

    scenarios: List[AdvisoryScenarioItem] = Field(min_length=2)
    assumptions: List[str] = Field(min_length=1)
    unknowns: List[str] = Field(default_factory=list)
    note: str = Field(default="", max_length=900)
    disclaimer: str = Field(default=DISCLAIMER_PL, min_length=1)

    @field_validator("disclaimer")
    @classmethod
    def _disclaimer_is_the_canonical_phrase(cls, v: str) -> str:
        """Дисклеймер — фиксированная юридическая формулировка, не свободный текст.

        Поиск подстроки здесь не работает в обе стороны: «Niniejsza analiza nie
        stanowi doradztwa podatkowego» — правильная фраза, которую подстрочная
        проверка отвергала, а «…to jest pełne doradztwo podatkowe» — прямо
        противоположное утверждение, которое она пропускала. Поэтому сверка
        идёт с канонической константой целиком.

        Нужна другая формулировка — меняется DISCLAIMER_PL, и меняется
        осознанно: это юридическая граница из CLAUDE.md, а не поле для правки
        на лету.
        """
        if _normalise_disclaimer(v) != _normalise_disclaimer(DISCLAIMER_PL):
            raise ValueError(
                "disclaimer обязан совпадать с канонической формулировкой "
                f"DISCLAIMER_PL: «{DISCLAIMER_PL}»"
            )
        return v


# =========================================================================
# 5. TAX OBLIGATIONS & PROFILE (Вспомогательные структуры)
# =========================================================================

class EmploymentPeriod(BaseModel):
    """Структура периода занятости в профиле налогоплательщика."""
    emp_type: EmploymentTypeKind
    start_date: date
    end_date: Optional[date] = None
    monthly_gross_avg: Decimal = Decimal('0.00')
    payer_nip: Optional[str] = None


class TaxpayerProfile(BaseModel):
    """Профиль налогоплательщика (JDG / физлицо)."""
    pesel_masked: str
    nip: str
    full_name_masked: str = "Jan Kowalski"
    date_of_birth: date
    is_student_under_26: bool = False
    is_vat_payer: bool = True
    jdg_tax_regime: Optional[TaxRegime] = TaxRegime.SKALA
    jdg_ryczalt_rate: Decimal = Decimal('0.12')
    employment_history: List[EmploymentPeriod] = Field(default_factory=list)


class ZUSObligations(BaseModel):
    """Месячный расчет страховых взносов ZUS."""
    stage: ZUSStage
    month: str # YYYY-MM
    spoleczne_base: Decimal = Decimal('0.00')
    zdrowotna_base: Decimal = Decimal('0.00')
    emerytalne: Decimal = Decimal('0.00')
    rentowe: Decimal = Decimal('0.00')
    chorobowe: Decimal = Decimal('0.00')
    wypadkowe: Decimal = Decimal('0.00')
    fundusz_pracy: Decimal = Decimal('0.00')
    skladka_zdrowotna: Decimal = Decimal('0.00')
    total_spoleczne: Decimal = Decimal('0.00')
    total_zus_do_zaplaty: Decimal = Decimal('0.00')
    forms_required: List[str] = Field(default_factory=list)
    zbieg_tytulow: bool = False


class SyncState(BaseModel):
    """Водяные знаки состояния (Шаг +0)."""
    nip: str
    telegram_last_message_id: Optional[int] = None
    ksef_last_sync_timestamp: Optional[str] = None
    bank_last_sync_timestamp: Optional[str] = None
    synced_sha256_hashes: List[str] = Field(default_factory=list)
