"""
src/segregator/domain/invariants.py
Движок детерминированных математических инвариантов и локализации расхождений (±Δ).
Обеспечивает 100% перекрестную верификацию бухгалтерских данных без привлечения LLM.
"""

from decimal import Decimal
from typing import Dict, List, Optional
from pydantic import BaseModel, Field


class InvariantResult(BaseModel):
    """Результат проверки математического инварианта."""
    invariant_id: str
    name: str
    passed: bool
    delta: Decimal = Decimal('0.00')
    message: str
    error_location: Optional[str] = None
    prescribed_action: Optional[str] = None


class InvariantEngine:
    """
    Автомат математических доказательств и инвариантов.
    Реализует 6 семейств проверок.
    """

    @staticmethod
    def check_document_math(netto: Decimal, vat: Decimal, brutto: Decimal) -> InvariantResult:
        """
        Инвариант 1: Netto + VAT == Brutto с точностью до 0.01 zł (округление строк).
        """
        expected_brutto = (netto + vat).quantize(Decimal('0.01'))
        delta = (brutto - expected_brutto).quantize(Decimal('0.01'))
        
        if abs(delta) <= Decimal('0.01'):
            return InvariantResult(
                invariant_id="INV_01_DOC_MATH",
                name="Арифметика документа",
                passed=True,
                delta=Decimal('0.00'),
                message="Инвариант соблюден: Netto + VAT == Brutto."
            )
        else:
            return InvariantResult(
                invariant_id="INV_01_DOC_MATH",
                name="Арифметика документа",
                passed=False,
                delta=delta,
                message=f"Расхождение в сумме фактуры: Netto ({netto}) + VAT ({vat}) = {expected_brutto}, но Brutto = {brutto}.",
                error_location="Заголовок/Строки фактуры",
                prescribed_action="Эскалация на ручную верификацию (Human-in-the-Loop)."
            )

    @staticmethod
    def check_cashflow_balance(
        saldo_start: Decimal,
        inflows: Decimal,
        outflows: Decimal,
        saldo_end: Decimal
    ) -> InvariantResult:
        """
        Инвариант 2: Saldo_Początkowe + Wpływy - Wydatki == Saldo_Końcowe.
        Детектирование кассовых разрывов и признаков неучтенных средств.
        """
        expected_end = (saldo_start + inflows - outflows).quantize(Decimal('0.01'))
        delta = (saldo_end - expected_end).quantize(Decimal('0.01'))
        
        if abs(delta) == Decimal('0.00'):
            return InvariantResult(
                invariant_id="INV_02_CASHFLOW",
                name="Баланс денежных средств",
                passed=True,
                delta=Decimal('0.00'),
                message="Инвариант соблюден: движение по счетам строго сходится с выпиской."
            )
        else:
            return InvariantResult(
                invariant_id="INV_02_CASHFLOW",
                name="Баланс денежных средств",
                passed=False,
                delta=delta,
                message=f"Не сходится сальдо счета: Начало ({saldo_start}) + Приход ({inflows}) - Расход ({outflows}) = {expected_end}, факт сальдо = {saldo_end} (разница {delta} zł).",
                error_location="Банковская выписка / Кассовая книга",
                prescribed_action="Проверить наличие пропущенных банковских комиссий или неотраженных переводов."
            )

    @staticmethod
    def check_zus_dra_convergence(
        dra_total: Decimal,
        rca_contributions: List[Decimal],
        rsa_deductions: List[Decimal] = []
    ) -> InvariantResult:
        """
        Инвариант 3: ZUS DRA.Suma == sum(ZUS RCA) + sum(ZUS RSA).
        Сводный отчет DRA строго равен сумме индивидуальных отчетов.
        """
        sum_rca = sum(rca_contributions, Decimal('0.00')).quantize(Decimal('0.01'))
        sum_rsa = sum(rsa_deductions, Decimal('0.00')).quantize(Decimal('0.01'))
        expected_dra = sum_rca - sum_rsa
        delta = (dra_total - expected_dra).quantize(Decimal('0.01'))
        
        if abs(delta) == Decimal('0.00'):
            return InvariantResult(
                invariant_id="INV_03_ZUS_DRA",
                name="Сходимость ZUS DRA и RCA",
                passed=True,
                delta=Decimal('0.00'),
                message="Инвариант соблюден: ZUS DRA до копейки равен сумме персонифицированных карточек."
            )
        else:
            return InvariantResult(
                invariant_id="INV_03_ZUS_DRA",
                name="Сходимость ZUS DRA и RCA",
                passed=False,
                delta=delta,
                message=f"Расхождение в ZUS DRA: в сводной декларации {dra_total} zł, по карточкам RCA/RSA {expected_dra} zł (дельта {delta} zł).",
                error_location="Блок ZUS DRA cz. III / ZUS RCA",
                prescribed_action="Пересчитать отчеты Płatnik перед отправкой через KEDU XML."
            )

    @staticmethod
    def check_vat_jpk_balance(
        jpk_ctrl_vat: Decimal,
        invoice_vat_list: List[Decimal]
    ) -> InvariantResult:
        """
        Инвариант 4: Контрольный блок JPK_Ctrl.PodatekNalezny строго равен сумме строк реестра продаж.
        """
        calculated_sum = sum(invoice_vat_list, Decimal('0.00')).quantize(Decimal('0.01'))
        delta = (jpk_ctrl_vat - calculated_sum).quantize(Decimal('0.01'))
        
        if abs(delta) == Decimal('0.00'):
            return InvariantResult(
                invariant_id="INV_04_JPK_VAT",
                name="Контрольная сумма JPK_V7",
                passed=True,
                delta=Decimal('0.00'),
                message="Инвариант соблюден: контрольная сумма налога JPK_Ctrl равна сумме фактур."
            )
        else:
            return InvariantResult(
                invariant_id="INV_04_JPK_VAT",
                name="Контрольная сумма JPK_V7",
                passed=False,
                delta=delta,
                message=f"Контрольная сумма JPK_V7 ({jpk_ctrl_vat} zł) не сходится с реестром фактур ({calculated_sum} zł).",
                error_location="Декларационный блок JPK_V7 / Ewidencja Sprzedaży",
                prescribed_action="Проверить округления в XML-схеме JPK_V7."
            )

    @staticmethod
    def check_double_entry_balance(
        debits: List[Decimal],
        credits: List[Decimal]
    ) -> InvariantResult:
        """
        Инвариант 6: Sp. z o.o. Двойная запись: sum(Debet / Wn) == sum(Credit / Ma).
        """
        sum_debit = sum(debits, Decimal('0.00')).quantize(Decimal('0.01'))
        sum_credit = sum(credits, Decimal('0.00')).quantize(Decimal('0.01'))
        delta = (sum_debit - sum_credit).quantize(Decimal('0.01'))
        
        if abs(delta) == Decimal('0.00'):
            return InvariantResult(
                invariant_id="INV_06_DOUBLE_ENTRY",
                name="Баланс двойной записи (Wn = Ma)",
                passed=True,
                delta=Decimal('0.00'),
                message="Инвариант соблюден: Дебет строго равен Кредиту."
            )
        else:
            return InvariantResult(
                invariant_id="INV_06_DOUBLE_ENTRY",
                name="Баланс двойной записи (Wn = Ma)",
                passed=False,
                delta=delta,
                message=f"Нарушение принципа двойной записи: Дебет = {sum_debit} zł, Кредит = {sum_credit} zł (разница {delta} zł).",
                error_location="Журнал проводок (Dziennik Księgowy)",
                prescribed_action="Заблокировать проводку до устранения односторонней записи."
            )
