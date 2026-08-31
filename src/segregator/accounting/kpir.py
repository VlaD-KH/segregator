"""
src/segregator/accounting/kpir.py
Бухгалтерский движок KPiR (Podatkowa Księga Przychodów i Rozchodów) и классификатор затрат.
Автоматически разносит суммы по колонкам 7, 8, 10, 11, 12, 13 с учетом лимитов на авто (75%/100%).
"""

from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field

from src.segregator.domain.models import DocumentFacts, BookingProposal


class KPiRColumn(int, Enum):
    """Колонки Книги доходов и расходов (KPiR)."""
    LP = 1                  # № п/п
    DATA_ZDARZENIA = 2      # Дата события
    NR_DOWODU = 3           # Номер документа
    KONTRAHENT_NAZWA = 4    # Контрагент: имя/название
    KONTRAHENT_ADRES = 5    # Контрагент: адрес
    OPIS_ZDARZENIA = 6      # Описание операции
    PRZYCHOD_SPRZEDAZ = 7   # Доход от реализации товаров и услуг
    POZOSTALE_PRZYCHODY = 8 # Прочие доходы
    PRZYCHODY_RAZEM = 9     # Итого доходов (кол. 7 + 8)
    ZAKUP_TOWAROW = 10      # Покупка торговых товаров и материалов
    KOSZTY_UBOCZNE = 11     # Побочные затраты на покупку (доставка)
    WYNAGRODZENIA = 12      # Зарплата работникам
    POZOSTALE_WYDATKI = 13  # Прочие расходы (офис, софт, бензин 75%, аренда)
    WYDATKI_RAZEM = 14      # Итого расходов (кол. 10 + 11 + 12 + 13)
    UWAGI = 16              # Примечания


class KPiREntry(BaseModel):
    """Одна строка бухгалтерской записи в KPiR."""
    lp: int
    entry_date: date
    doc_number: str
    counterparty_name: str
    counterparty_address: str = ""
    description: str
    
    col_7_przychody: Decimal = Decimal('0.00')
    col_8_pozostale_przychody: Decimal = Decimal('0.00')
    col_9_razem_przychody: Decimal = Decimal('0.00')
    
    col_10_zakup_towarow: Decimal = Decimal('0.00')
    col_11_koszty_uboczne: Decimal = Decimal('0.00')
    col_12_wynagrodzenia: Decimal = Decimal('0.00')
    col_13_pozostale_wydatki: Decimal = Decimal('0.00')
    col_14_razem_wydatki: Decimal = Decimal('0.00')
    
    vat_amount: Decimal = Decimal('0.00')
    is_vat_deductible: bool = True
    notes: Optional[str] = None


class KPiREngine:
    """Детерминированный процессор разнесения фактов документов по колонкам KPiR."""

    @classmethod
    def book_document(
        cls,
        facts: DocumentFacts,
        proposal: BookingProposal,
        lp: int = 1,
        is_company_vat_payer: bool = True
    ) -> KPiREntry:
        """
        Преобразует DocumentFacts и BookingProposal в готовую строку KPiR.
        Учитывает статус плательщика VAT и лимиты расходов на авто (75% KUP).
        """
        raw_netto = Decimal(str(facts.netto.value)) if facts.netto and facts.netto.value is not None else Decimal('0.00')
        raw_vat = Decimal(str(facts.vat.value)) if facts.vat and facts.vat.value is not None else Decimal('0.00')
        raw_brutto = Decimal(str(facts.brutto.value)) if facts.brutto and facts.brutto.value is not None else (raw_netto + raw_vat)
        
        doc_date = facts.doc_date.value if (facts.doc_date and isinstance(facts.doc_date.value, date)) else date.today()
        doc_nr = str(facts.doc_number.value) if (facts.doc_number and facts.doc_number.value) else "DOW_01"
        seller_name = str(facts.seller_name.value) if (facts.seller_name and facts.seller_name.value) else "Kontrahent"
        
        entry = KPiREntry(
            lp=lp,
            entry_date=doc_date,
            doc_number=doc_nr,
            counterparty_name=seller_name,
            description=proposal.category
        )

        # 1. ДОХОДЫ (Przychody)
        if proposal.kpir_column == 7 or facts.doc_type == "faktura_sprzedazy":
            # Если плательщик VAT -> доход равен Netto, если неплательщик -> доход равен Brutto
            revenue = raw_netto if is_company_vat_payer else raw_brutto
            entry.col_7_przychody = revenue
            entry.col_9_razem_przychody = revenue
            entry.vat_amount = raw_vat
            return entry

        if proposal.kpir_column == 8:
            revenue = raw_netto if is_company_vat_payer else raw_brutto
            entry.col_8_pozostale_przychody = revenue
            entry.col_9_razem_przychody = revenue
            return entry

        # 2. РАСХОДЫ (Wydatki)
        # Расчет признаваемой суммы расхода (KUP) с учетом лимита на авто (75% / 100% / 20%)
        # Для легкового авто со смешанным использованием:
        # Невычитаемый VAT = 50% * VAT
        # База расхода = Netto + 50% * VAT
        # В KUP идет = 75% * (Netto + 50% * VAT)
        if proposal.vehicle_usage_type == "mixed":
            # 50% вычет НДС
            non_deductible_vat = (raw_vat * Decimal('0.50')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            expense_base = raw_netto + non_deductible_vat
            tax_deductible_expense = (expense_base * Decimal('0.75')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            entry.vat_amount = (raw_vat * Decimal('0.50')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            entry.notes = "Samochód osobowy: 75% KUP, 50% VAT"
        elif not is_company_vat_payer:
            # Неплательщик VAT включает весь брутто в KUP
            tax_deductible_expense = (raw_brutto * proposal.kup_deductible_ratio).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            entry.vat_amount = Decimal('0.00')
        else:
            # Стандартный расход (100% KUP, 100% VAT)
            tax_deductible_expense = (raw_netto * proposal.kup_deductible_ratio).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            entry.vat_amount = raw_vat

        # Разнесение по колонкам затрат
        if proposal.kpir_column == 10:
            entry.col_10_zakup_towarow = tax_deductible_expense
        elif proposal.kpir_column == 11:
            entry.col_11_koszty_uboczne = tax_deductible_expense
        elif proposal.kpir_column == 12:
            entry.col_12_wynagrodzenia = tax_deductible_expense
        else: # По умолчанию колонка 13 (Pozostałe wydatki)
            entry.col_13_pozostale_wydatki = tax_deductible_expense

        entry.col_14_razem_wydatki = (
            entry.col_10_zakup_towarow +
            entry.col_11_koszty_uboczne +
            entry.col_12_wynagrodzenia +
            entry.col_13_pozostale_wydatki
        )

        return entry
