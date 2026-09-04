"""
tests/test_schema_validation.py
Строгая верификация соответствия Pydantic-моделей домена официальным JSON-схемам:
- agents/schemas/document_facts.json
- agents/schemas/booking_proposal.json
- agents/schemas/payroll_facts.json
- agents/schemas/advisory_report.json
"""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
import jsonschema
import pytest

from segregator.domain.models import (
    DataSource,
    DocumentType,
    AgentDecision,
    PeriodDateBasis,
    EmploymentTypeKind,
    PayrollSource,
    ZUSStage,
    ExtractedField,
    DocumentFacts,
    BookingProposal,
    PayrollPeriodItem,
    PayrollFacts,
    AdvisoryScenarioItem,
    AdvisoryReport,
)
from segregator.advisor.doradca import AgentDoradca


@pytest.fixture
def schemas_dir():
    root = Path(__file__).resolve().parent.parent
    return root / "agents" / "schemas"


def test_document_facts_schema_validation(schemas_dir):
    """Проверка валидации DocumentFacts против document_facts.json."""
    schema_path = schemas_dir / "document_facts.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    facts = DocumentFacts(
        doc_type=DocumentType.FAKTURA_KOSZTOWA,
        fields={
            "nip_sprzedawcy": ExtractedField(value="5252344078", source=DataSource.KSEF, confidence=1.0),
            "nip_nabywcy": ExtractedField(value="1234567890", source=DataSource.KSEF, confidence=1.0),
            "nazwa_sprzedawcy": ExtractedField(value="PKN ORLEN S.A.", source=DataSource.KSEF, confidence=1.0),
            "nr_dokumentu": ExtractedField(value="FV/2026/08/001", source=DataSource.KSEF, confidence=1.0),
            "data_wystawienia": ExtractedField(value="2026-08-31", source=DataSource.KSEF, confidence=1.0),
            "data_sprzedazy": ExtractedField(value="2026-08-31", source=DataSource.KSEF, confidence=1.0),
            "termin_platnosci": ExtractedField(value="2026-09-14", source=DataSource.KSEF, confidence=1.0),
            "netto": ExtractedField(value=1000.0, source=DataSource.KSEF, confidence=1.0),
            "vat": ExtractedField(value=230.0, source=DataSource.KSEF, confidence=1.0),
            "brutto": ExtractedField(value=1230.0, source=DataSource.KSEF, confidence=1.0),
            "stawka_vat": ExtractedField(value=0.23, source=DataSource.KSEF, confidence=1.0),
            "waluta": ExtractedField(value="PLN", source=DataSource.KSEF, confidence=1.0),
        },
        decision=AgentDecision.OK,
        why="Pomyślnie zweryfikowano fakturę w KSeF."
    )

    data = json.loads(facts.model_dump_json(exclude_none=True))
    # Валидация бросит исключение, если не соответствует схеме
    jsonschema.validate(instance=data, schema=schema)


def test_booking_proposal_schema_validation(schemas_dir):
    """Проверка валидации BookingProposal против booking_proposal.json."""
    schema_path = schemas_dir / "booking_proposal.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    proposal = BookingProposal(
        category="Koszty eksploatacji pojazdu",
        subcategory="paliwo",
        kpir_column=13,
        vat_rate=0.23,
        vat_deduction_ratio=0.5,
        pit_cost_ratio=0.75,
        period_date="2026-08-31",
        period_date_basis=PeriodDateBasis.DATA_WYSTAWIENIA,
        confidence=0.98,
        basis="rule:car_mixed_75",
        decision=AgentDecision.OK,
        why="Samochód osobowy w trybie mieszanym (75% KUP / 50% VAT)."
    )

    data = json.loads(proposal.model_dump_json(exclude_none=True))
    jsonschema.validate(instance=data, schema=schema)


def test_payroll_facts_schema_validation(schemas_dir):
    """Проверка валидации PayrollFacts против payroll_facts.json."""
    schema_path = schemas_dir / "payroll_facts.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    payroll = PayrollFacts(
        periods=[
            PayrollPeriodItem(
                **{
                    "from": "2025-01-01",
                    "to": "2025-05-31",
                    "kind": EmploymentTypeKind.UOP,
                    "payer_nip": "1112223344",
                    "gross": 50000.0,
                    "kup": 1250.0,
                    "advance_withheld": 3525.0,
                    "source": PayrollSource.PIT_11
                }
            ),
            PayrollPeriodItem(
                **{
                    "from": "2025-10-01",
                    "to": "2025-12-31",
                    "kind": EmploymentTypeKind.JDG,
                    "payer_nip": "5252344078",
                    "gross": 45000.0,
                    "kup": 9000.0,
                    "advance_withheld": 4320.0,
                    "source": PayrollSource.PROFILE
                }
            )
        ],
        zus_stage_by_month={
            "2025-10": ZUSStage.ULGA_NA_START,
            "2025-11": ZUSStage.ULGA_NA_START,
            "2025-12": ZUSStage.ULGA_NA_START
        },
        zbieg_tytulow=False,
        decision=AgentDecision.OK,
        why="Historia zatrudnienia za 2025 rok kompletna."
    )

    data = json.loads(payroll.model_dump_json(by_alias=True, exclude_none=True))
    jsonschema.validate(instance=data, schema=schema)


def test_advisory_report_schema_validation(schemas_dir):
    """Проверка валидации AdvisoryReport против advisory_report.json."""
    schema_path = schemas_dir / "advisory_report.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    report = AgentDoradca.compare_tax_regimes(
        annual_revenue=Decimal('200000.00'),
        annual_costs=Decimal('40000.00'),
        ryczalt_rate=Decimal('0.12'),
        social_zus_annual=Decimal('4500.00')
    )

    data = json.loads(report.model_dump_json(exclude_none=True))
    jsonschema.validate(instance=data, schema=schema)


# ===========================================================================
# Обратная сторона: модель обязана ОТВЕРГАТЬ то, что отвергает схема
# ===========================================================================
# Тесты выше односторонние: они строят заведомо валидный объект и проверяют,
# что он проходит схему. Так нельзя поймать ОСЛАБЛЕНИЕ контракта — снятие
# min_length с модели не даёт ни одного красного теста, и модель со схемой
# расходятся в сторону послабления бесшумно. Ниже — обратное направление.

from pydantic import ValidationError  # noqa: E402


def _schema(name: str) -> dict:
    path = Path(__file__).resolve().parents[1] / "agents" / "schemas" / f"{name}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _one_scenario():
    return AdvisoryScenarioItem(name="Skala", figures={"przychod": 1.0}, tradeoff="t")


def test_advisory_report_requires_at_least_two_scenarios():
    """Схема требует minItems: 2 — модель обязана требовать столько же."""
    assert _schema("advisory_report")["properties"]["scenarios"]["minItems"] == 2
    with pytest.raises(ValidationError):
        AdvisoryReport(scenarios=[_one_scenario()], assumptions=["a"])


def test_advisory_report_requires_assumptions():
    """Сравнение режимов без единого допущения — не отчёт советника."""
    assert _schema("advisory_report")["properties"]["assumptions"]["minItems"] == 1
    with pytest.raises(ValidationError):
        AdvisoryReport(scenarios=[_one_scenario(), _one_scenario()], assumptions=[])


def test_advisory_report_rejects_empty_disclaimer():
    """Юридическая граница: пустой дисклеймер отвергается моделью."""
    assert _schema("advisory_report")["properties"]["disclaimer"]["minLength"] == 1
    with pytest.raises(ValidationError):
        AdvisoryReport(
            scenarios=[_one_scenario(), _one_scenario()],
            assumptions=["a"],
            disclaimer="",
        )


def test_booking_proposal_rejects_ratios_outside_the_enum():
    """vat_deduction_ratio и pit_cost_ratio ограничены перечислением схемы."""
    s = _schema("booking_proposal")["properties"]
    assert set(s["vat_deduction_ratio"]["enum"]) == {0, 0.5, 1, None}
    assert set(s["pit_cost_ratio"]["enum"]) == {0, 0.75, 1, None}

    base = dict(category="Koszty", confidence=1.0, basis="rule:test", decision=AgentDecision.OK)
    with pytest.raises(ValidationError):
        BookingProposal(**base, vat_deduction_ratio=0.75)  # 0.75 — доля PIT, не VAT
    with pytest.raises(ValidationError):
        BookingProposal(**base, pit_cost_ratio=0.5)  # 0.5 — доля VAT, не PIT


def test_extracted_field_rejects_unknown_source():
    """Источник поля — закрытое перечисление: llm/ocr/ksef/…, но не что попало."""
    with pytest.raises(ValidationError):
        ExtractedField(value="x", source="wikipedia", confidence=1.0)


def test_extracted_field_confidence_is_bounded():
    """Уверенность вне [0, 1] — не уверенность."""
    with pytest.raises(ValidationError):
        ExtractedField(value="x", source=DataSource.OCR, confidence=1.4)
    with pytest.raises(ValidationError):
        ExtractedField(value="x", source=DataSource.OCR, confidence=-0.1)


def test_models_forbid_extra_fields_like_their_schemas():
    """additionalProperties: false в схеме = extra="forbid" в модели.

    Иначе модель молча примет поле, которого в контракте нет, и оно уедет в БД.
    """
    for name in ("document_facts", "booking_proposal", "advisory_report"):
        assert _schema(name)["additionalProperties"] is False, name

    with pytest.raises(ValidationError):
        BookingProposal(
            category="Koszty",
            confidence=1.0,
            basis="rule:test",
            decision=AgentDecision.OK,
            nieznane_pole="сюрприз",
        )
