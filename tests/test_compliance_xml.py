"""
tests/test_compliance_xml.py
Тесты для генераторов XML деклараций JPK_V7M, ZUS KEDU и валидатора ComplianceXMLValidator.
"""

from datetime import date
from decimal import Decimal
import pytest

from src.segregator.domain.models import ZUSObligations, ZUSStage
from src.segregator.compliance.jpk_v7 import (
    JPKSalesRecord,
    JPKPurchaseRecord,
    JPKV7MGenerator,
)
from src.segregator.compliance.zus_kedu import ZUSKEDUGenerator
from src.segregator.compliance.xml_validator import ComplianceXMLValidator


def test_jpk_v7m_generation_and_validation():
    """
    Тест генерации XML JPK_V7M и валидации контрольных сумм.
    """
    sales = [
        JPKSalesRecord(
            lp_sprzedazy=1,
            nr_kontrahenta="1234567890",
            nazwa_kontrahenta="Klient ABC Sp. z o.o.",
            dowod_sprzedazy="FV/2025/11/001",
            data_wystawienia=date(2025, 11, 10),
            data_sprzedazy=date(2025, 11, 10),
            gtu_12=1, # IT Services
            k_19_netto_23=Decimal('10000.00'),
            k_20_vat_23=Decimal('2300.00')
        )
    ]
    
    purchases = [
        JPKPurchaseRecord(
            lp_zakupu=1,
            nr_dostawcy="5252344078",
            nazwa_dostawcy="PKN ORLEN S.A.",
            dowod_zakupu="FV/2025/11/042",
            data_zakupu=date(2025, 11, 15),
            data_wplywu=date(2025, 11, 15),
            k_42_netto_pozostale=Decimal('1000.00'),
            k_43_vat_pozostale=Decimal('115.00') # 50% от 230 zł
        )
    ]

    xml_out = JPKV7MGenerator.generate_xml(
        taxpayer_nip="5252344078",
        taxpayer_full_name="Jan Kowalski",
        year=2025,
        month=11,
        sales_records=sales,
        purchase_records=purchases
    )

    assert "<?xml version=" in xml_out
    assert "JPK_VAT" in xml_out
    assert "<GTU_12>1</GTU_12>" in xml_out
    assert "<PodatekNalezny>2300.00</PodatekNalezny>" in xml_out
    assert "<PodatekNaliczony>115.00</PodatekNaliczony>" in xml_out

    # Валидация
    val_res = ComplianceXMLValidator.validate_jpk_v7m(xml_out)
    assert val_res.is_valid is True
    assert val_res.details["LiczbaWierszySprzedazy"] == "1"
    assert val_res.details["PodatekNalezny"] == "2300.00"
    assert val_res.details["PodatekNaliczony"] == "115.00"


def test_zus_kedu_dra_generation_and_validation():
    """
    Тест генерации XML пакета ZUS KEDU (ZUS DRA) и проверки реквизитов.
    """
    obligations = ZUSObligations(
        stage=ZUSStage.ULGA_NA_START,
        month="2025-11",
        spoleczne_base=Decimal('0.00'),
        zdrowotna_base=Decimal('12000.00'),
        emerytalne=Decimal('0.00'),
        rentowe=Decimal('0.00'),
        chorobowe=Decimal('0.00'),
        wypadkowe=Decimal('0.00'),
        fundusz_pracy=Decimal('0.00'),
        skladka_zdrowotna=Decimal('1080.00'),
        total_spoleczne=Decimal('0.00'),
        total_zus_do_zaplaty=Decimal('1080.00'),
        forms_required=["ZUS DRA", "ZUS ZZA"]
    )

    xml_out = ZUSKEDUGenerator.generate_zus_dra_xml(
        taxpayer_nip="5252344078",
        taxpayer_regon="123456789",
        taxpayer_pesel_masked="880512*****",
        taxpayer_last_name="Kowalski",
        taxpayer_first_name="Jan",
        zus_obligations=obligations
    )

    assert "<?xml version=" in xml_out
    assert "<KEDU" in xml_out
    assert "<ZUSDRA>" in xml_out
    assert "112025" in xml_out

    # Валидация
    val_res = ComplianceXMLValidator.validate_zus_kedu(xml_out)
    assert val_res.is_valid is True
    assert val_res.details["NIP"] == "5252344078"
    assert val_res.details["Razem_ZUS_DoZaplaty"] == "1080.00"
