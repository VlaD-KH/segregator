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

from segregator.domain.models import DocumentFacts, BookingProposal, DocumentType


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
        raw_netto = facts.netto
        raw_vat = facts.vat
        raw_brutto = facts.brutto if facts.brutto > Decimal('0.00') else (raw_netto + raw_vat)
        
        doc_date = facts.doc_date or date.today()
        doc_nr = facts.doc_number or "DOW_01"
        seller_name = facts.seller_name or "Kontrahent"
        
        entry = KPiREntry(
            lp=lp,
            entry_date=doc_date,
            doc_number=doc_nr,
            counterparty_name=seller_name,
            description=proposal.category
        )

        # 1. ДОХОДЫ (Przychody)
        if proposal.kpir_column == 7 or facts.doc_type == DocumentType.FAKTURA_SPRZEDAZY:
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
        # Лимит авто: pit_cost_ratio == 0.75 и vat_deduction_ratio == 0.50
        pit_ratio = Decimal(str(proposal.pit_cost_ratio)) if proposal.pit_cost_ratio is not None else Decimal('1.00')
        vat_ratio = Decimal(str(proposal.vat_deduction_ratio)) if proposal.vat_deduction_ratio is not None else Decimal('1.00')

        if proposal.pit_cost_ratio == 0.75 and proposal.vat_deduction_ratio == 0.50:
            # 50% невычитаемый VAT входит в базу затрат KUP
            non_deductible_vat = (raw_vat * Decimal('0.50')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            expense_base = raw_netto + non_deductible_vat
            tax_deductible_expense = (expense_base * Decimal('0.75')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            entry.vat_amount = (raw_vat * Decimal('0.50')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            entry.notes = "Samochód osobowy: 75% KUP, 50% VAT"
        elif not is_company_vat_payer:
            tax_deductible_expense = (raw_brutto * pit_ratio).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            entry.vat_amount = Decimal('0.00')
        else:
            tax_deductible_expense = (raw_netto * pit_ratio).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            entry.vat_amount = (raw_vat * vat_ratio).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

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
