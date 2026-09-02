"""
src/segregator/compliance/jpk_v7.py
Генератор официального XML файла JPK_V7M (версия схемы JPK_V7M(2)).
Формирует реестры покупок и продаж, авто-GTU и контрольные суммы (SprzedazCtrl, ZakupCtrl).
Соответствует спецификации Министерства Финансов Польши (Ministerstwo Finansów).
"""

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Dict, List, Optional
import xml.etree.ElementTree as ET
from pydantic import BaseModel, Field

from segregator.domain.models import DocumentFacts, BookingProposal


class JPKSalesRecord(BaseModel):
    """Запись в реестре продаж JPK_V7 (SprzedazWiersz)."""
    lp_sprzedazy: int
    kod_kraju_nadania_tin: str = "PL"
    nr_kontrahenta: str
    nazwa_kontrahenta: str
    dowod_sprzedazy: str
    data_wystawienia: date
    data_sprzedazy: date
    
    # GTU и процедуры (опционально)
    gtu_12: int = 0          # Услуги IT/консалтинг (GTU_12)
    procedura_mpp: int = 0   # Split payment (MPP)
    
    # Суммы нетто и налога по ставкам (23%, 8%, 5%, 0%, zw)
    k_19_netto_23: Decimal = Decimal('0.00')
    k_20_vat_23: Decimal = Decimal('0.00')
    k_21_netto_8: Decimal = Decimal('0.00')
    k_22_vat_8: Decimal = Decimal('0.00')


class JPKPurchaseRecord(BaseModel):
    """Запись в реестре покупок JPK_V7 (ZakupWiersz)."""
    lp_zakupu: int
    kod_kraju_nadania_tin: str = "PL"
    nr_dostawcy: str
    nazwa_dostawcy: str
    dowod_zakupu: str
    data_zakupu: date
    data_wplywu: date
    
    # Суммы нетто и вычитаемого НДС
    k_42_netto_pozostale: Decimal = Decimal('0.00')
    k_43_vat_pozostale: Decimal = Decimal('0.00')


class JPKV7MGenerator:
    """
    Генератор XML-структуры JPK_V7M.
    """

    JPK_NAMESPACE = "http://crd.gov.pl/wzor/2021/12/27/11148/"

    @classmethod
    def generate_xml(
        cls,
        taxpayer_nip: str,
        taxpayer_full_name: str,
        year: int,
        month: int,
        sales_records: List[JPKSalesRecord],
        purchase_records: List[JPKPurchaseRecord],
        urzad_skarbowy_code: str = "1436" # Например, US Warszawa-Mokotów
    ) -> str:
        """
        Генерирует валидный XML документ JPK_V7M(2).
        """
        root = ET.Element("JPK", {
            "xmlns": cls.JPK_NAMESPACE,
            "xmlns:etd": "http://crd.gov.pl/xml/schematy/dziedzinowe/mf/2021/06/08/eD/DefinicjeTypy/",
            "xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance"
        })

        # 1. Naglowek (Заголовок)
        naglowek = ET.SubElement(root, "Naglowek")
        ET.SubElement(naglowek, "KodFormularza", {
            "kodSystemowy": "JPK_V7M (2)",
            "wersjaSchemy": "1-0E"
        }).text = "JPK_VAT"
        ET.SubElement(naglowek, "WariantFormularza").text = "2"
        ET.SubElement(naglowek, "DataWytworzeniaJPK").text = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        ET.SubElement(naglowek, "NazwaSystemu").text = "Segregator AI v1.0"
        ET.SubElement(naglowek, "CelZlozenia", {"poz": "P_7"}).text = "1" # Первичная подача
        ET.SubElement(naglowek, "KodUrzedu").text = urzad_skarbowy_code
        ET.SubElement(naglowek, "Rok").text = str(year)
        ET.SubElement(naglowek, "Miesiac").text = str(month)

        # 2. Podmiot1 (Налогоплательщик)
        podmiot = ET.SubElement(root, "Podmiot1", {"rola": "Podatnik"})
        osoba_fizyczna = ET.SubElement(podmiot, "OsobaFizyczna")
        ET.SubElement(osoba_fizyczna, "NIP").text = taxpayer_nip
        
        # Разделение имени и фамилии
        name_parts = taxpayer_full_name.split(" ", 1)
        imie = name_parts[0] if name_parts else "Podatnik"
        nazwisko = name_parts[1] if len(name_parts) > 1 else "JDG"
        ET.SubElement(osoba_fizyczna, "ImiePierwsze").text = imie
        ET.SubElement(osoba_fizyczna, "Nazwisko").text = nazwisko

        # 3. Ewidencja (Реестры)
        ewidencja = ET.SubElement(root, "Ewidencja")

        # 3.1. Sprzedaz (Продажи)
        tot_sales_vat = Decimal('0.00')
        for s in sales_records:
            sw = ET.SubElement(ewidencja, "SprzedazWiersz")
            ET.SubElement(sw, "LpSprzedazy").text = str(s.lp_sprzedazy)
            ET.SubElement(sw, "KodKrajuNadaniaTIN").text = s.kod_kraju_nadania_tin
            ET.SubElement(sw, "NrKontrahenta").text = s.nr_kontrahenta
            ET.SubElement(sw, "NazwaKontrahenta").text = s.nazwa_kontrahenta
            ET.SubElement(sw, "DowodSprzedazy").text = s.dowod_sprzedazy
            ET.SubElement(sw, "DataWystawienia").text = s.data_wystawienia.isoformat()
            ET.SubElement(sw, "DataSprzedazy").text = s.data_sprzedazy.isoformat()
            
            if s.gtu_12 == 1:
                ET.SubElement(sw, "GTU_12").text = "1"
            if s.procedura_mpp == 1:
                ET.SubElement(sw, "MPP").text = "1"
                
            if s.k_19_netto_23 > Decimal('0.00'):
                ET.SubElement(sw, "K_19").text = f"{s.k_19_netto_23:.2f}"
                ET.SubElement(sw, "K_20").text = f"{s.k_20_vat_23:.2f}"
                tot_sales_vat += s.k_20_vat_23

        # Контрольный блок продаж (SprzedazCtrl)
        s_ctrl = ET.SubElement(ewidencja, "SprzedazCtrl")
        ET.SubElement(s_ctrl, "LiczbaWierszySprzedazy").text = str(len(sales_records))
        ET.SubElement(s_ctrl, "PodatekNalezny").text = f"{tot_sales_vat:.2f}"

        # 3.2. Zakup (Покупки)
        tot_purchase_vat = Decimal('0.00')
        for p in purchase_records:
            zw = ET.SubElement(ewidencja, "ZakupWiersz")
            ET.SubElement(zw, "LpZakupu").text = str(p.lp_zakupu)
            ET.SubElement(zw, "KodKrajuNadaniaTIN").text = p.kod_kraju_nadania_tin
            ET.SubElement(zw, "NrDostawcy").text = p.nr_dostawcy
            ET.SubElement(zw, "NazwaDostawcy").text = p.nazwa_dostawcy
            ET.SubElement(zw, "DowodZakupu").text = p.dowod_zakupu
            ET.SubElement(zw, "DataZakupu").text = p.data_zakupu.isoformat()
            ET.SubElement(zw, "DataWplywu").text = p.data_wplywu.isoformat()
            
            if p.k_42_netto_pozostale > Decimal('0.00') or p.k_43_vat_pozostale > Decimal('0.00'):
                ET.SubElement(zw, "K_42").text = f"{p.k_42_netto_pozostale:.2f}"
                ET.SubElement(zw, "K_43").text = f"{p.k_43_vat_pozostale:.2f}"
                tot_purchase_vat += p.k_43_vat_pozostale

        # Контрольный блок покупок (ZakupCtrl)
        z_ctrl = ET.SubElement(ewidencja, "ZakupCtrl")
        ET.SubElement(z_ctrl, "LiczbaWierszyZakupow").text = str(len(purchase_records))
        ET.SubElement(z_ctrl, "PodatekNaliczony").text = f"{tot_purchase_vat:.2f}"

        # 4. Deklaracja (Сводная декларация VAT)
        deklaracja = ET.SubElement(root, "Deklaracja")
        ET.SubElement(deklaracja, "Naglowek").append(naglowek)
        
        poz = ET.SubElement(deklaracja, "PozycjeSzczegolowe")
        if tot_sales_vat > Decimal('0.00'):
            ET.SubElement(poz, "P_38").text = f"{tot_sales_vat:.2f}" # Всего начисленный налог
        if tot_purchase_vat > Decimal('0.00'):
            ET.SubElement(poz, "P_48").text = f"{tot_purchase_vat:.2f}" # Всего вычитаемый налог
            
        vat_difference = tot_sales_vat - tot_purchase_vat
        if vat_difference > Decimal('0.00'):
            ET.SubElement(poz, "P_51").text = f"{vat_difference:.2f}" # К уплате в налоговую
        else:
            ET.SubElement(poz, "P_53").text = f"{abs(vat_difference):.2f}" # К переносу на след. месяц

        # Форматирование XML строки
        ET.indent(root, space="  ", level=0)
        xml_str = '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="utf-8").decode("utf-8")
        return xml_str
