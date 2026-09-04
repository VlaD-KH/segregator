"""
tests/test_masking.py
Маскирование чувствительных полей перед записью в БД.

docs/DATA_BOUNDARY.md, инвариант 3: PESEL, IBAN и номера карт маскируются везде,
кроме самого генерируемого официального документа. `logging.py` закрывает логи,
эти тесты закрывают путь «факты -> kpir_entries.raw_facts_json».
"""

from datetime import date
from decimal import Decimal
import json
import sqlite3

import pytest

from segregator.domain.models import (
    AgentDecision,
    DataSource,
    DocumentFacts,
    DocumentType,
    EmploymentPeriod,
    EmploymentTypeKind,
    ExtractedField,
    TaxRegime,
    TaxpayerProfile,
    is_sensitive_field_name,
    mask_iban,
    mask_sensitive_fields,
)
from segregator.service import SegregatorService

IBAN = "PL61109010140000071219812874"


def test_mask_iban_keeps_only_head_and_tail():
    assert mask_iban(IBAN) == "PL**...2874"
    assert mask_iban("PL 61 1090 1014 0000 0712 1981 2874") == "PL**...2874"


def test_mask_iban_edge_cases():
    assert mask_iban(None) is None
    assert mask_iban("") is None
    assert mask_iban("PL61") == "****"  # слишком коротко, чтобы показывать хвост


@pytest.mark.parametrize(
    "name",
    [
        "iban",
        "konto",
        "numer_konta",
        "nr_konta",
        # Родительный падеж: `rachunek` литералом сюда не попадал, а
        # `numer_rachunku` — самое частое имя счёта на польской фактуре.
        "rachunek_bankowy",
        "numer_rachunku",
        "nr_rachunku",
        "rachunek_bankowy_sprzedawcy",
        "swift",
        "pesel",
        "card_number",
        "numer_karty",
        "numer_karty_platniczej",
    ],
)
def test_sensitive_names_detected(name):
    """Счёт могут назвать по-разному — точечное совпадение по «iban» их не ловило."""
    assert is_sensitive_field_name(name) is True


@pytest.mark.parametrize(
    "name",
    [
        "nip_sprzedawcy",
        "netto",
        "vat",
        "nazwa_sprzedawcy",
        "data_wystawienia",
        # Слова, начинающиеся с тех же основ: слишком жадный шаблон затёр бы
        # имя контрагента и номенклатуру вместе с номерами счетов и карт.
        "kontrahent",
        "kartoteka",
        "kartka_pocztowa",
    ],
)
def test_business_fields_not_masked(name):
    """NIP — открытый идентификатор предприятия, инвариант 3 его не перечисляет."""
    assert is_sensitive_field_name(name) is False


def test_mask_sensitive_fields_masks_account_named_anything():
    facts = {
        "fields": {
            "numer_konta": {"value": IBAN, "source": "ocr", "confidence": 0.9},
            "pesel": {"value": "90010112345", "source": "ocr", "confidence": 0.9},
            "nip_sprzedawcy": {"value": "5252344078", "source": "ocr", "confidence": 1.0},
            "netto": {"value": 1000.0, "source": "ocr", "confidence": 1.0},
        }
    }
    masked = mask_sensitive_fields(facts)
    assert masked["fields"]["numer_konta"]["value"] == "PL**...2874"
    assert masked["fields"]["pesel"]["value"] == "****"
    # Не-чувствительные поля не тронуты.
    assert masked["fields"]["nip_sprzedawcy"]["value"] == "5252344078"
    assert masked["fields"]["netto"]["value"] == 1000.0


def test_account_number_never_reaches_database(tmp_path):
    """Номер счёта не должен лечь в raw_facts_json открытым текстом.

    Поле названо `numer_konta`, а не `iban`: прежняя реализация сверялась с
    единственным литеральным ключом «iban» и такой документ пропускала.
    """
    service = SegregatorService(workspace_root=tmp_path)
    profile = TaxpayerProfile(
        pesel_masked="900101*****",
        nip="5252344078",
        date_of_birth=date(1990, 1, 1),
        jdg_tax_regime=TaxRegime.SKALA,
        employment_history=[
            EmploymentPeriod(emp_type=EmploymentTypeKind.JDG, start_date=date(2025, 10, 1))
        ],
    )

    doc = tmp_path / "faktura.txt"
    doc.write_text("Faktura kosztowa", encoding="utf-8")

    facts = DocumentFacts(
        doc_type=DocumentType.FAKTURA_KOSZTOWA,
        fields={
            "nip_sprzedawcy": ExtractedField(value="5252344078", source=DataSource.OCR, confidence=0.98),
            "nazwa_sprzedawcy": ExtractedField(value="PKN ORLEN S.A.", source=DataSource.OCR, confidence=0.98),
            "nr_dokumentu": ExtractedField(value="FV/2025/11/100", source=DataSource.OCR, confidence=0.98),
            "data_wystawienia": ExtractedField(value="2025-11-10", source=DataSource.OCR, confidence=0.98),
            "netto": ExtractedField(value=1000.0, source=DataSource.OCR, confidence=0.98),
            "vat": ExtractedField(value=230.0, source=DataSource.OCR, confidence=0.98),
            "brutto": ExtractedField(value=1230.0, source=DataSource.OCR, confidence=0.98),
            "numer_konta": ExtractedField(value=IBAN, source=DataSource.OCR, confidence=0.95),
            # Родительный падеж — самое частое имя счёта на польской фактуре.
            "numer_rachunku": ExtractedField(value=IBAN, source=DataSource.OCR, confidence=0.95),
        },
        decision=AgentDecision.OK,
    )

    state = service.process_document(doc, profile, custom_facts=facts)
    assert state.kpir_entry is not None

    conn = sqlite3.connect(service.db_path)
    try:
        rows = conn.execute("SELECT raw_facts_json FROM kpir_entries").fetchall()
    finally:
        conn.close()

    assert rows, "запись KPiR не создана — тест ничего не проверяет"
    for (raw,) in rows:
        assert IBAN not in raw, "номер счёта лёг в БД открытым текстом"
        stored = json.loads(raw)
        assert stored["fields"]["numer_konta"]["value"] == "PL**...2874"
        assert stored["fields"]["numer_rachunku"]["value"] == "PL**...2874"


# ---------------------------------------------------------------------------
# Контракт «значение поля — скаляр»: допущение, на котором держится маскирование
# ---------------------------------------------------------------------------

def test_schema_keeps_field_values_scalar():
    """Схема обязана держать `value` скалярным — на этом стоит маскирование.

    `mask_sensitive_fields` обходит один уровень словаря `fields`, и это
    сознательно: `document_facts.json` разрешает `value` только
    string|number|null. Если кто-то добавит вложенность (позиции фактуры
    `pozycje[]`, под-объект `platnosc` с реквизитами), маскирование перестанет
    быть полным — и узнать об этом надо здесь, а не из утечки в БД.
    """
    import json
    from pathlib import Path

    schema_path = Path(__file__).resolve().parents[1] / "agents" / "schemas" / "document_facts.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    value_schema = schema["properties"]["fields"]["additionalProperties"]["properties"]["value"]
    assert set(value_schema["type"]) == {"string", "number", "null"}, (
        "document_facts.json изменил тип `value`. Маскирование в "
        "mask_sensitive_fields обходит только плоский уровень — прежде чем "
        "расширять схему, научите его обходить новую форму."
    )


def test_composite_sensitive_value_raises_instead_of_leaking():
    """Составное значение чувствительного поля — громкая ошибка, а не тихий пропуск."""
    facts = {
        "fields": {
            "numer_konta": {
                "value": {"iban": IBAN, "bank": "PKO"},
                "source": "ocr",
                "confidence": 0.9,
            }
        }
    }
    with pytest.raises(TypeError, match="составное"):
        mask_sensitive_fields(facts)
