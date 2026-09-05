"""Маска по ЗНАЧЕНИЮ поля, а не только по его имени.

Блокер 7 (serene-honking-flurry.md, 2.7). Проверка по имени поля ловит
`numer_konta` и `nr_rachunku`, но для `wyciag_bankowy` и
`potwierdzenie_przelewu` счёт лежит в значении нейтрально названного поля —
`opis_operacji`, `tytul_przelewu`. Их имя ни о чём не говорит, и такой
документ проходил маску насквозь.

Решает контрольная сумма ISO 7064 mod-97-10: совпадение по форме ничего не
значит, иначе под маску уехали бы NIP, номер фактуры и сумма.
"""

from datetime import date
import json
import sqlite3

import pytest

from segregator.domain.masking import (
    mask_ibans_in_text,
    mask_sensitive_fields,
    validate_iban_mod97,
)
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
)
from segregator.service import SegregatorService

IBAN = "PL61109010140000071219812874"
IBAN_SPACED = "PL61 1090 1014 0000 0712 1981 2874"
NRB_BARE = "61109010140000071219812874"


# --- контрольная сумма ----------------------------------------------------------


@pytest.mark.parametrize("value", [IBAN, IBAN_SPACED, NRB_BARE, "PL61-1090-1014-0000-0712-1981-2874"])
def test_real_iban_validates_in_any_printed_form(value):
    """На фактуре счёт печатают группами, с префиксом и без."""
    assert validate_iban_mod97(value) is True


@pytest.mark.parametrize(
    "value",
    [
        "PL61109010140000071219812875",   # испорчена последняя цифра
        "5252344078",                     # NIP
        "12345678901234567890123456",     # 26 цифр, но не счёт
        "",
        "FV/2025/11/100",
    ],
)
def test_non_iban_is_rejected(value):
    assert validate_iban_mod97(value) is False


# --- маска внутри текста --------------------------------------------------------


def test_iban_inside_text_is_masked_and_the_rest_survives():
    """Терять назначение платежа, чтобы спрятать счёт, — порча, а не защита."""
    text = f"Przelew na rachunek {IBAN_SPACED} tytulem FV/2025/11/100"
    masked = mask_ibans_in_text(text)

    assert IBAN_SPACED not in masked
    assert "PL**...2874" in masked
    assert masked.startswith("Przelew na rachunek ")
    assert masked.endswith("tytulem FV/2025/11/100")


def test_numbers_that_are_not_accounts_are_left_alone():
    text = "NIP 5252344078, kwota 1230.00 PLN, data 2025-11-10, FV/2025/11/100"
    assert mask_ibans_in_text(text) == text


# --- маска по значению в фактах -------------------------------------------------


@pytest.mark.parametrize("field_name", ["opis_operacji", "tytul_przelewu", "uwagi"])
def test_account_in_a_neutrally_named_field_is_masked(field_name):
    facts = {
        "fields": {
            field_name: {"value": f"Wplata na {IBAN}", "source": "ocr", "confidence": 0.9},
        }
    }
    masked = mask_sensitive_fields(facts)
    assert IBAN not in masked["fields"][field_name]["value"]
    assert "PL**...2874" in masked["fields"][field_name]["value"]


def test_business_values_are_not_touched():
    facts = {
        "fields": {
            "nazwa_sprzedawcy": {"value": "PKN ORLEN S.A.", "source": "ocr", "confidence": 1.0},
            "nip_sprzedawcy": {"value": "5252344078", "source": "ocr", "confidence": 1.0},
            "netto": {"value": 1000.0, "source": "ocr", "confidence": 1.0},
        }
    }
    masked = mask_sensitive_fields(facts)
    assert masked["fields"]["nazwa_sprzedawcy"]["value"] == "PKN ORLEN S.A."
    assert masked["fields"]["nip_sprzedawcy"]["value"] == "5252344078"
    assert masked["fields"]["netto"]["value"] == 1000.0


def test_why_is_masked():
    """`why` — объяснение агента, куда он свободно цитирует документ.
    Уходило в БД без маски вообще."""
    facts = {"why": f"Nie zgadza sie rachunek {IBAN} w tytule", "fields": {}}
    masked = mask_sensitive_fields(facts)
    assert IBAN not in masked["why"]
    assert "PL**...2874" in masked["why"]


# --- сквозной путь до БД --------------------------------------------------------


def test_account_in_operation_description_never_reaches_database(tmp_path):
    """Тот самый случай из 2.7: выписка, где счёт в описании операции."""
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

    doc = tmp_path / "wyciag.txt"
    doc.write_text("wyciag bankowy", encoding="utf-8")

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
            # Имя поля нейтральное — по имени маска сюда не смотрит.
            "opis_operacji": ExtractedField(
                value=f"Przelew na {IBAN_SPACED} za paliwo", source=DataSource.OCR, confidence=0.95
            ),
        },
        decision=AgentDecision.OK,
    )

    service.process_document(doc, profile, custom_facts=facts)

    conn = sqlite3.connect(service.db_path)
    try:
        rows = conn.execute("SELECT raw_facts_json FROM kpir_entries").fetchall()
    finally:
        conn.close()

    assert rows, "проводка не создана — тест ничего не проверяет"
    for (raw,) in rows:
        assert IBAN not in raw, "счёт лёг в БД открытым текстом"
        assert IBAN_SPACED not in raw
        stored = json.loads(raw)
        assert "PL**...2874" in stored["fields"]["opis_operacji"]["value"]
        assert "za paliwo" in stored["fields"]["opis_operacji"]["value"]
