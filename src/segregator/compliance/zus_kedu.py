"""
src/segregator/compliance/zus_kedu.py
Генератор официальных XML файлов ZUS KEDU (версия 5.4) для импорта в программу Płatnik и портал eZUS / PUE ZUS.
Формирует бланки ZUS DRA (декларация за плательщика) и ZUS RCA (именной отчет за застрахованного).
"""

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Dict, List, Optional
import xml.etree.ElementTree as ET

from segregator.domain.models import ZUSObligations, ZUSStage


class ZUSKEDUGenerator:
    """
    Генератор XML-структуры пакета KEDU 5.4 (ZUS DRA / RCA).
    """

    KEDU_SCHEMA_VERSION = "5.4"

    @classmethod
    def generate_zus_dra_xml(
        cls,
        taxpayer_nip: str,
        taxpayer_regon: str,
        taxpayer_pesel_masked: str,
        taxpayer_last_name: str,
        taxpayer_first_name: str,
        zus_obligations: ZUSObligations,
        bank_account_nrs: str = "PL00000000000000000000000000"
    ) -> str:
        """
        Генерирует официальный XML документ ZUS DRA (Декларация расчетная).
        """
        root = ET.Element("KEDU", {
            "wersja_schematu": cls.KEDU_SCHEMA_VERSION,
            "xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance"
        })

        # Заголовок пакета
        naglowek = ET.SubElement(root, "naglowek.KEDU")
        ET.SubElement(naglowek, "program.nadawca").text = "Segregator AI"
        ET.SubElement(naglowek, "wersja.programu").text = "1.0.0"
        ET.SubElement(naglowek, "data.utworzenia.KEDU").text = datetime.now(timezone.utc).strftime("%d-%m-%Y")

        # Документ ZUS DRA
        dra = ET.SubElement(root, "ZUSDRA")
        
        # I. Dane organizacyjne
        bl_i = ET.SubElement(dra, "I")
        ET.SubElement(bl_i, "p1").text = "01" # Идентификатор декларации (01 = обычная месячная)
        # Парсинг месяца YYYY-MM
        y, m = zus_obligations.month.split("-")
        ET.SubElement(bl_i, "p2").text = f"{m}{y}" # Например "112025"

        # II. Dane identyfikacyjne płatnika składek
        bl_ii = ET.SubElement(dra, "II")
        ET.SubElement(bl_ii, "p1").text = taxpayer_nip
        if taxpayer_regon:
            ET.SubElement(bl_ii, "p2").text = taxpayer_regon
        ET.SubElement(bl_ii, "p3").text = taxpayer_pesel_masked
        ET.SubElement(bl_ii, "p6").text = taxpayer_last_name
        ET.SubElement(bl_ii, "p7").text = taxpayer_first_name

        # IV. Zestawienie składek na ubezpieczenia społeczne
        bl_iv = ET.SubElement(dra, "IV")
        ET.SubElement(bl_iv, "p1").text = f"{zus_obligations.emerytalne:.2f}"
        ET.SubElement(bl_iv, "p2").text = f"{zus_obligations.rentowe:.2f}"
        ET.SubElement(bl_iv, "p3").text = f"{zus_obligations.emerytalne + zus_obligations.rentowe:.2f}"
        ET.SubElement(bl_iv, "p4").text = f"{zus_obligations.chorobowe:.2f}"
        ET.SubElement(bl_iv, "p5").text = f"{zus_obligations.wypadkowe:.2f}"
        ET.SubElement(bl_iv, "p7").text = f"{zus_obligations.total_spoleczne:.2f}" # Итого społeczne

        # VI. Zestawienie należnych składek na ubezpieczenie zdrowotne
        bl_vi = ET.SubElement(dra, "VI")
        ET.SubElement(bl_vi, "p1").text = f"{zus_obligations.zdrowotna_base:.2f}"
        ET.SubElement(bl_vi, "p2").text = f"{zus_obligations.skladka_zdrowotna:.2f}"
        ET.SubElement(bl_vi, "p7").text = f"{zus_obligations.skladka_zdrowotna:.2f}" # Итого zdrowotna

        # VIII. Zestawienie należnych składek na Fundusz Pracy i Fundusz Solidarnościowy
        bl_viii = ET.SubElement(dra, "VIII")
        ET.SubElement(bl_viii, "p1").text = f"{zus_obligations.fundusz_pracy:.2f}"

        # IX. Zestawienie należnych składek do zapłaty (Razem ZUS)
        bl_ix = ET.SubElement(dra, "IX")
        ET.SubElement(bl_ix, "p1").text = f"{zus_obligations.total_spoleczne:.2f}"
        ET.SubElement(bl_ix, "p2").text = f"{zus_obligations.skladka_zdrowotna:.2f}"
        ET.SubElement(bl_ix, "p3").text = f"{zus_obligations.fundusz_pracy:.2f}"
        ET.SubElement(bl_ix, "p4").text = f"{zus_obligations.total_zus_do_zaplaty:.2f}" # Итого к оплате на счет ZUS NRS

        # Форматирование XML
        ET.indent(root, space="  ", level=0)
        xml_str = '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="utf-8").decode("utf-8")
        return xml_str
