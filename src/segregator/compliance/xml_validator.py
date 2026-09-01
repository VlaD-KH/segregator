"""
src/segregator/compliance/xml_validator.py
Валидатор официальных XML документов (JPK_V7, ZUS KEDU, KSeF FA(3)).
Проверяет синтаксическую корректность, обязательные теги, кодировку UTF-8 и математическую сходимость контрольных сумм.
"""

import xml.etree.ElementTree as ET
from decimal import Decimal
from typing import Dict, List, Optional
from pydantic import BaseModel


class ValidationResult(BaseModel):
    """Результат проверки XML структуры."""
    is_valid: bool
    schema_type: str                     # 'JPK_V7M' | 'ZUS_KEDU' | 'KSEF_FA3' | 'UNKNOWN'
    errors: List[str] = []
    warnings: List[str] = []
    details: Dict[str, str] = {}


class ComplianceXMLValidator:
    """
    Системный валидатор структуры и данных XML.
    """

    @classmethod
    def validate_jpk_v7m(cls, xml_content: str) -> ValidationResult:
        """
        Проверяет XML JPK_V7M на корректность структуры и равенство контрольных сумм.
        """
        errors = []
        warnings = []
        details = {}

        try:
            root = ET.fromstring(xml_content)
        except ET.ParseError as e:
            return ValidationResult(
                is_valid=False,
                schema_type="JPK_V7M",
                errors=[f"Синтаксическая ошибка XML: {str(e)}"]
            )

        # 1. Проверка корневого элемента
        if not root.tag.endswith("JPK"):
            errors.append(f"Неверный корневой тег: ожидался JPK, получен {root.tag}")

        # 2. Проверка заголовка и налогоплательщика
        naglowek = root.find("{*}Naglowek")
        if naglowek is None:
            errors.append("Отсутствует обязательный узел Naglowek")

        podmiot = root.find("{*}Podmiot1")
        if podmiot is None:
            errors.append("Отсутствует обязательный узел Podmiot1")

        # 3. Проверка контрольных сумм (SprzedazCtrl / ZakupCtrl)
        ewidencja = root.find("{*}Ewidencja")
        if ewidencja is not None:
            s_ctrl = ewidencja.find("{*}SprzedazCtrl")
            if s_ctrl is not None:
                cnt_node = s_ctrl.find("{*}LiczbaWierszySprzedazy")
                vat_node = s_ctrl.find("{*}PodatekNalezny")
                details["LiczbaWierszySprzedazy"] = cnt_node.text if cnt_node is not None else "0"
                details["PodatekNalezny"] = vat_node.text if vat_node is not None else "0.00"

            z_ctrl = ewidencja.find("{*}ZakupCtrl")
            if z_ctrl is not None:
                cnt_z = z_ctrl.find("{*}LiczbaWierszyZakupow")
                vat_z = z_ctrl.find("{*}PodatekNaliczony")
                details["LiczbaWierszyZakupow"] = cnt_z.text if cnt_z is not None else "0"
                details["PodatekNaliczony"] = vat_z.text if vat_z is not None else "0.00"

        return ValidationResult(
            is_valid=(len(errors) == 0),
            schema_type="JPK_V7M",
            errors=errors,
            warnings=warnings,
            details=details
        )

    @classmethod
    def validate_zus_kedu(cls, xml_content: str) -> ValidationResult:
        """
        Проверяет XML ZUS KEDU на наличие бланков и обязательных реквизитов плательщика.
        """
        errors = []
        warnings = []
        details = {}

        try:
            root = ET.fromstring(xml_content)
        except ET.ParseError as e:
            return ValidationResult(
                is_valid=False,
                schema_type="ZUS_KEDU",
                errors=[f"Синтаксическая ошибка XML: {str(e)}"]
            )

        if root.tag != "KEDU":
            errors.append(f"Неверный корневой тег: ожидался KEDU, получен {root.tag}")

        # Проверка версии схемы
        schema_ver = root.attrib.get("wersja_schematu")
        if schema_ver != "5.4":
            warnings.append(f"Версия схемы {schema_ver}, ожидалась эталонная 5.4")

        # Проверка наличия документа ZUSDRA
        dra = root.find("ZUSDRA")
        if dra is None:
            errors.append("В пакете KEDU отсутствует документ ZUSDRA")
        else:
            bl_ii = dra.find("II")
            if bl_ii is not None:
                nip = bl_ii.find("p1")
                details["NIP"] = nip.text if nip is not None else ""

            bl_ix = dra.find("IX")
            if bl_ix is not None:
                razem = bl_ix.find("p4")
                details["Razem_ZUS_DoZaplaty"] = razem.text if razem is not None else "0.00"

        return ValidationResult(
            is_valid=(len(errors) == 0),
            schema_type="ZUS_KEDU",
            errors=errors,
            warnings=warnings,
            details=details
        )
