"""Маскирование чувствительных значений перед любой записью наружу.

docs/DATA_BOUNDARY.md, инвариант 3: PESEL, номера счетов и карт не покидают
машину открытым текстом и не ложатся в БД, логи и выхлоп probe/.

Модуль вынесен из `domain/models.py` отдельно намеренно: маска — зона R, и в
неё должен уезжать этот файл, а не весь модуль моделей вместе с ним.

Две проверки, а не одна:

1. **По имени поля.** `numer_konta`, `nr_rachunku`, `pesel` — значение
   маскируется целиком, каким бы оно ни было.
2. **По значению.** Для `wyciag_bankowy` и `potwierdzenie_przelewu` счёт лежит
   в ЗНАЧЕНИИ нейтрально названного поля — `opis_operacji`, `tytul_przelewu`, —
   и проверка по имени его не видит. Поэтому значения всех полей сканируются
   на IBAN с проверкой контрольной суммы ISO 7064 mod-97-10.

Контрольной суммы PESEL в репозитории нет, поэтому по значению он не ловится —
только по имени поля. F-3.7 остаётся невыполненной, и это записано, а не забыто.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

__all__ = [
    "is_sensitive_field_name",
    "mask_ibans_in_text",
    "mask_iban",
    "mask_sensitive_fields",
    "validate_iban_mod97",
]


def mask_iban(iban: Optional[str]) -> Optional[str]:
    """Маскирование IBAN для соблюдения RODO/DATA_BOUNDARY."""
    if not iban:
        return None
    cleaned = re.sub(r"\s+", "", iban)
    if len(cleaned) < 8:
        return "****"
    return f"{cleaned[:2]}**...{cleaned[-4:]}"


# Имена полей, значения которых не имеют права лечь в БД открытым текстом
# (docs/DATA_BOUNDARY.md, инвариант 3). Ключ «iban» — лишь один из способов
# назвать счёт: реальные извлечения дают numer_konta / nr_konta / rachunek,
# и точечное совпадение по строке "iban" их не ловило.
# Польские имена полей склоняются, и точный литерал ловит только один падеж:
# `rachunek` не совпадает с `numer_rachunku` — а это САМОЕ частое название
# банковского счёта на фактуре. Поэтому основы, а не полные слова.
#   kont[oa]  — konto, numer_konta; но не kontrahent (там `kontr`)
#   rachun    — rachunek, numer_rachunku, rachunek_bankowy_sprzedawcy
#   kart[ayęi]— karta, numer_karty, kartę; но не kartoteka/kartka (там `karto`/`kartk`)
# Перекос сознательно в сторону лишней маски: не замаскировать счёт дороже,
# чем замаскировать поле, которое счётом не было.
# `kont[oa](?!kt)` — иначе основа съедала `kontakt`, `dane_kontaktowe`,
# `kontakt_email`, и адрес почты переписывался в фальшивый IBAN.
# `dowod` и `kart` как основы: `dowod_osobistego`, `nr_dowodu`, `kartą` —
# те же падежи, ради которых основы и вводились.
_ACCOUNT_FIELD_RE = re.compile(r"iban|kont[oa](?!kt)|rachun|account|swift|bic", re.IGNORECASE)
_IDENTITY_FIELD_RE = re.compile(r"pesel|card_?number|kart[ayęąi]|dowod", re.IGNORECASE)

# NIP сознательно не маскируется: это открытый идентификатор предприятия,
# инвариант 3 перечисляет PESEL, IBAN и номера карт.

# Кандидат в IBAN: 26 цифр польского NRB, с необязательным префиксом PL и
# любыми одиночными разделителями между цифрами — на фактуре счёт печатают
# группами по четыре. Само по себе совпадение ничего не значит: решение
# принимает контрольная сумма ниже, поэтому NIP, номер фактуры и дата
# под маску не попадают.
_IBAN_CANDIDATE_RE = re.compile(r"(?:PL[\s\-]?)?(?:\d[\s\-]?){25}\d", re.IGNORECASE)


def is_sensitive_field_name(name: str) -> bool:
    """Нужно ли маскировать значение поля с таким именем."""
    return bool(_ACCOUNT_FIELD_RE.search(name) or _IDENTITY_FIELD_RE.search(name))


def validate_iban_mod97(iban: str) -> bool:
    """Польский IBAN (PL + 26 цифр) по ISO 7064 mod-97-10.

    Поднято из `probe/verify_ground_truth_pipeline.py`, где эта проверка уже
    была написана, — с более терпимой очисткой: в тексте счёт разделяют
    пробелами и дефисами.
    """
    clean = re.sub(r"[^0-9A-Za-z]", "", iban or "").upper()
    if clean.startswith("PL"):
        clean = clean[2:]
    if len(clean) != 26 or not clean.isdigit():
        return False
    lk, bban = clean[:2], clean[2:]
    # 'PL' -> 2521, контрольные цифры уезжают в хвост (ISO 13616)
    return int(bban + "2521" + lk) % 97 == 1


def mask_ibans_in_text(text: str) -> str:
    """Заменить в тексте каждый настоящий IBAN на маску, остальное не трогая.

    Маскируется подстрока, а не всё поле: `opis_operacji` несёт назначение
    платежа, без которого проводку не объяснить. Терять его, чтобы спрятать
    счёт, — не защита, а порча.
    """
    if not text:
        return text

    def _replace(match: re.Match[str]) -> str:
        candidate = match.group(0)
        if not validate_iban_mod97(candidate):
            return candidate
        return mask_iban(candidate) or "****"

    return _IBAN_CANDIDATE_RE.sub(_replace, text)


def mask_sensitive_fields(facts_dict: Dict[str, Any]) -> Dict[str, Any]:
    """Замаскировать чувствительные данные в сериализованных фактах (на месте).

    Вызывается перед любой записью фактов в БД.
    """
    why = facts_dict.get("why")
    if isinstance(why, str):
        # `why` — объяснение агента, куда он свободно цитирует документ.
        # Раньше уходило в БД без маски вообще.
        facts_dict["why"] = mask_ibans_in_text(why)

    fields = facts_dict.get("fields")
    if not isinstance(fields, dict):
        return facts_dict

    for name, field in fields.items():
        if not isinstance(field, dict):
            continue
        value = field.get("value")
        if value is None:
            continue

        sensitive_by_name = is_sensitive_field_name(name)

        # Схема держит `value` скалярным (string|number|null), и рекурсивный
        # обход здесь был бы защитой от формы, которую контракт запрещает.
        # Но если контракт когда-нибудь изменится, молча пропустить составное
        # значение мимо маски нельзя — падаем громко.
        if isinstance(value, (dict, list)):
            if not sensitive_by_name:
                continue
            raise TypeError(
                f"Поле «{name}» чувствительное, но значение составное "
                f"({type(value).__name__}). Схема document_facts.json допускает "
                f"только string|number|null; маскирование составных значений "
                f"не реализовано — реализуйте его прежде, чем менять схему."
            )

        if sensitive_by_name:
            field["value"] = (
                mask_iban(str(value)) if _ACCOUNT_FIELD_RE.search(name) else "****"
            )
        elif isinstance(value, str):
            # Имя поля нейтральное — решает содержимое. Так ловятся выписка и
            # подтверждение перевода, где счёт лежит в описании операции.
            field["value"] = mask_ibans_in_text(value)

    return facts_dict
