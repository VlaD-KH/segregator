"""
src/segregator/compliance/pit36.py
Консолидатор годовой налоговой декларации PIT-36 с приложением PIT/B (Польша).
Обрабатывает сложные сценарии смены статуса физлица внутри года (UoP -> UZ -> JDG).
Объединяет доходы по найму, договорам поручения и предпринимательской деятельности.
"""

from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, List, Optional
from pydantic import BaseModel, Field

from src.segregator.tax.pit import PITCalculator, PITConstants


class IncomeSourceRecord(BaseModel):
    """Строка дохода по конкретному источнику за налоговый год."""
    source_name: str                     # 'UoP' | 'UZ' | 'JDG'
    source_description: str
    revenue_przychod: Decimal = Decimal('0.00')
    tax_costs_kup: Decimal = Decimal('0.00')
    income_dochod: Decimal = Decimal('0.00')
    social_zus_deductible: Decimal = Decimal('0.00')
    advances_paid: Decimal = Decimal('0.00')


class PITBAttachment(BaseModel):
    """Приложение PIT/B: Информация о доходах/убытках от предпринимательской деятельности."""
    nip: str
    business_name: str
    pkd_main: str = "62.01.Z"
    revenue: Decimal = Decimal('0.00')
    costs: Decimal = Decimal('0.00')
    income: Decimal = Decimal('0.00')
    loss: Decimal = Decimal('0.00')


class PIT36Declaration(BaseModel):
    """
    Консолидированная годовая декларация PIT-36 (версия 31).
    """
    tax_year: int = 2025
    taxpayer_pesel_masked: str
    taxpayer_nip: str
    taxpayer_name: str = "Jan K*****"
    
    # Источники доходов
    sources: List[IncomeSourceRecord] = Field(default_factory=list)
    pit_b: Optional[PITBAttachment] = None
    
    # Сводные показатели доходов и расходов (Раздел E)
    total_revenue: Decimal = Decimal('0.00')
    total_costs: Decimal = Decimal('0.00')
    total_income: Decimal = Decimal('0.00')
    
    # Вычеты (Раздел F: Социальные взносы ZUS)
    total_social_zus_deduction: Decimal = Decimal('0.00')
    
    # Налоговая база (Раздел G)
    tax_base_rounded: Decimal = Decimal('0.00')
    
    # Налог начисленный (Раздел H: 12% / 32% + Kwota wolna 30k)
    calculated_tax: Decimal = Decimal('0.00')
    
    # Зачет уплаченных авансов (Раздел I)
    total_advances_paid: Decimal = Decimal('0.00')
    
    # Итог: Налог к доплате или переплата к возврату
    tax_to_pay: Decimal = Decimal('0.00')
    tax_overpayment_refund: Decimal = Decimal('0.00')
    
    notes: List[str] = Field(default_factory=list)


class PIT36Consolidator:
    """
    Детерминированный консолидатор годовой формы PIT-36.
    """

    @classmethod
    def consolidate_year_2025(
        cls,
        pesel_masked: str,
        nip: str,
        uop_income: Optional[IncomeSourceRecord] = None,
        uz_income: Optional[IncomeSourceRecord] = None,
        jdg_pit_b: Optional[PITBAttachment] = None,
        jdg_social_zus_paid: Decimal = Decimal('0.00'),
        jdg_advances_paid: Decimal = Decimal('0.00'),
    ) -> PIT36Declaration:
        """
        Консолидирует доходы UoP (из PIT-11), UZ (из PIT-11) и JDG (из KPiR/PIT-B) за 2025 год.
        """
        sources = []
        tot_rev = Decimal('0.00')
        tot_cost = Decimal('0.00')
        tot_inc = Decimal('0.00')
        tot_social = Decimal('0.00')
        tot_advances = Decimal('0.00')
        notes = []

        # 1. Учет доходов по трудовому договору (UoP)
        if uop_income:
            sources.append(uop_income)
            tot_rev += uop_income.revenue_przychod
            tot_cost += uop_income.tax_costs_kup
            tot_inc += uop_income.income_dochod
            tot_social += uop_income.social_zus_deductible
            tot_advances += uop_income.advances_paid
            notes.append(f"Добавлен источник UoP: Доход {uop_income.income_dochod} zł (из PIT-11).")

        # 2. Учет доходов по договору поручения (UZ)
        if uz_income:
            sources.append(uz_income)
            tot_rev += uz_income.revenue_przychod
            tot_cost += uz_income.tax_costs_kup
            tot_inc += uz_income.income_dochod
            tot_social += uz_income.social_zus_deductible
            tot_advances += uz_income.advances_paid
            notes.append(f"Добавлен источник UZ: Доход {uz_income.income_dochod} zł (из PIT-11).")

        # 3. Учет доходов от предпринимательской деятельности (JDG + PIT/B)
        if jdg_pit_b:
            jdg_record = IncomeSourceRecord(
                source_name="JDG",
                source_description="Pozarolnicza działalność gospodarcza (KPiR)",
                revenue_przychod=jdg_pit_b.revenue,
                tax_costs_kup=jdg_pit_b.costs,
                income_dochod=jdg_pit_b.income,
                social_zus_deductible=jdg_social_zus_paid,
                advances_paid=jdg_advances_paid
            )
            sources.append(jdg_record)
            tot_rev += jdg_pit_b.revenue
            tot_cost += jdg_pit_b.costs
            tot_inc += jdg_pit_b.income
            tot_social += jdg_social_zus_paid
            tot_advances += jdg_advances_paid
            notes.append(f"Добавлен источник JDG (PIT/B): Доход {jdg_pit_b.income} zł.")

        # 4. Расчет совокупной налоговой базы
        # База = Совокупный доход - Совокупные социальные взносы ZUS
        raw_tax_base = max(Decimal('0.00'), tot_inc - tot_social)
        tax_base_rounded = raw_tax_base.quantize(Decimal('1'), rounding=ROUND_HALF_UP).quantize(Decimal('0.01'))

        # 5. Расчет единого налога по Skala podatkowa (12% / 32% + единая kwota wolna 30k zł)
        calculated_tax = PITCalculator.calculate_skala_tax(tax_base_rounded)

        # 6. Сверка с уплаченными авансами (Раздел I)
        tax_delta = calculated_tax - tot_advances
        
        if tax_delta > Decimal('0.00'):
            tax_to_pay = tax_delta.quantize(Decimal('1'), rounding=ROUND_HALF_UP).quantize(Decimal('0.01'))
            tax_overpayment = Decimal('0.00')
            notes.append(f"Итог к доплате в Urząd Skarbowy: {tax_to_pay} zł.")
        else:
            tax_to_pay = Decimal('0.00')
            tax_overpayment = abs(tax_delta).quantize(Decimal('1'), rounding=ROUND_HALF_UP).quantize(Decimal('0.01'))
            notes.append(f"Итог к возврату (Nadpłata): {tax_overpayment} zł.")

        return PIT36Declaration(
            tax_year=2025,
            taxpayer_pesel_masked=pesel_masked,
            taxpayer_nip=nip,
            sources=sources,
            pit_b=jdg_pit_b,
            total_revenue=tot_rev,
            total_costs=tot_cost,
            total_income=tot_inc,
            total_social_zus_deduction=tot_social,
            tax_base_rounded=tax_base_rounded,
            calculated_tax=calculated_tax,
            total_advances_paid=tot_advances,
            tax_to_pay=tax_to_pay,
            tax_overpayment_refund=tax_overpayment,
            notes=notes
        )
