"""Отбор кандидатов между discover и audit.

Прогон по Старгарду показал, чего не хватало: `audit` брал карточки по алфавиту
и уходил на заправки и скаутские дружины, а в масштабе города это тысячи
запросов к чужим сайтам. Отбор решает три вещи сразу — вежливость, стоимость и
осмысленность выборки.

Правила отбора — данные (`taxonomy/exclusions.yaml`), а не код: новая сеть или
новая мусорная категория добавляется строкой.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import yaml

from prospektor.config import MODULE_ROOT
from prospektor.models import Business
from prospektor.store import Store
from prospektor.taxonomy import expand_to_source_values
from prospektor.util import slug_name

EXCLUSIONS_PATH = MODULE_ROOT / "taxonomy" / "exclusions.yaml"

# Сигнал, который выставляет только стадия audit. Наличие любых других сигналов
# ничего не говорит: классификация адреса происходит раньше и без сети.
AUDIT_MARKER = "audit.completed_at"


@lru_cache(maxsize=1)
def _exclusions() -> dict[str, Any]:
    if not EXCLUSIONS_PATH.exists():
        return {}
    return yaml.safe_load(EXCLUSIONS_PATH.read_text(encoding="utf-8")) or {}


@lru_cache(maxsize=1)
def junk_categories() -> frozenset[str]:
    return frozenset(_exclusions().get("junk_categories", []))


@lru_cache(maxsize=1)
def chain_slugs() -> frozenset[str]:
    return frozenset(slug_name(n) for n in _exclusions().get("chain_names", []))


@lru_cache(maxsize=1)
def _junk_name_patterns() -> tuple[re.Pattern[str], ...]:
    return tuple(
        re.compile(p, re.IGNORECASE) for p in _exclusions().get("junk_name_patterns", [])
    )


def is_chain(name: str) -> bool:
    """Точка сети или франшизы.

    Сравнение по нормализованному имени: `slug_name` снимает юрформу и диакритику.
    Название сетевой точки почти всегда содержит номер или город — «Żabka Nr 1234»,
    «0143 ORLEN - Stargard», — поэтому ищем имя сети как последовательность слов
    внутри названия, а не только в его начале.
    """
    tokens = slug_name(name).split()
    if not tokens:
        return False
    chains = chain_slugs()
    for chain in chains:
        parts = chain.split()
        if not parts:
            continue
        span = len(parts)
        if any(tokens[i : i + span] == parts for i in range(len(tokens) - span + 1)):
            return True
    return False


def is_junk_name(name: str) -> bool:
    return any(p.search(name or "") for p in _junk_name_patterns())


@dataclass
class Rejection:
    business_id: str
    name: str
    reason: str


def _categories_of(biz: Business) -> set[str]:
    return {c.lower() for c in biz.categories}


def select(
    store: Store,
    *,
    categories: list[str] | None = None,
    limit: int | None = None,
    skip_audited: bool = True,
) -> tuple[list[Business], list[Rejection]]:
    """Кого имеет смысл аудировать. Возвращает отобранных и отброшенных с причиной.

    Причины возвращаются наружу намеренно: молчаливый отсев невозможно проверить,
    а ошибка в списке сетей иначе осталась бы незаметной.
    """
    wanted = expand_to_source_values(categories) if categories else None
    junk = junk_categories()

    chosen: list[Business] = []
    rejected: list[Rejection] = []

    for biz in store.iter_businesses():
        cats = _categories_of(biz)
        # Порядок причин — от структурной к частной: поштомат не является бизнесом
        # сам по себе, и это более точное объяснение, чем «точка сети».
        if is_junk_name(biz.name):
            rejected.append(Rejection(biz.id, biz.name, "название не является названием бизнеса"))
        elif cats & junk:
            rejected.append(Rejection(biz.id, biz.name, "нелидовая категория"))
        elif is_chain(biz.name):
            rejected.append(Rejection(biz.id, biz.name, "точка сети или франшизы"))
        elif wanted is not None and not (cats & wanted):
            rejected.append(Rejection(biz.id, biz.name, "вне выбранных категорий"))
        elif skip_audited and AUDIT_MARKER in store.signals_for(biz.id):
            rejected.append(Rejection(biz.id, biz.name, "уже проверен"))
        else:
            chosen.append(biz)

    # Сначала те, у кого заявлен собственный сайт: там аудит даёт больше сигналов,
    # а значит и более обоснованную оценку.
    chosen.sort(key=lambda b: (b.website is None, b.name))
    return (chosen[:limit] if limit else chosen), rejected


# Приставка типа улицы. Снимается, чтобы «al. Wojska Polskiego» и
# «Wojska Polskiego» считались одной улицей.
_ADDRESS_NOISE = re.compile(
    r"^\s*(?:ul\.|ulica|al\.|aleja|aleje|pl\.|plac|os\.|osiedle)\s+", re.IGNORECASE
)


def street_name(address: str) -> str:
    """Имя улицы без номера дома и всего, что за ним.

    Резать по одному лишь хвостовому номеру недостаточно: в данных Overture за
    номером тянется «3 piętro», «lokal 106», «pawilon 73». Поэтому отбрасывается
    всё, начиная с первого токена, который начинается с цифры.

    Исключение — улицы, чьё название само начинается с числа («1 Maja»): если
    после отсечения ничего не осталось, берётся адрес целиком.
    """
    cleaned = _ADDRESS_NOISE.sub("", address or "")
    tokens = cleaned.split()
    head = []
    for token in tokens:
        if token[:1].isdigit():
            break
        head.append(token)
    return slug_name(" ".join(head)) or slug_name(cleaned)


@dataclass
class SuspectMerge:
    """Карточка, собранная из записей с разными адресами.

    Чаще всего это дубликаты одного бизнеса из разных источников Overture, и
    склейка верна: «Wojska Polskiego 11/4» и «Wojska Polskiego 13A/2» — одно и
    то же место, записанное по-разному. Настоящий повод для подозрений — когда
    улицы разные: либо заведение переезжало и Overture хранит старую запись,
    либо это два разных бизнеса с одинаковым названием.

    Отличить одно от другого автоматически нельзя, поэтому список выносится
    человеку — но отсортированным так, чтобы сомнительное было сверху.
    """

    business_id: str
    name: str
    addresses: list[str]
    sources: list[str]

    @property
    def streets(self) -> set[str]:
        return {s for s in (street_name(a) for a in self.addresses) if s}

    @property
    def different_streets(self) -> bool:
        """Разные улицы — вот это стоит смотреть в первую очередь."""
        return len(self.streets) > 1


def suspect_merges(
    store: Store, limit: int = 50, only_different_streets: bool = False
) -> list[SuspectMerge]:
    """Склейки, которые стоит проверить глазами. Сомнительные — первыми."""
    rows = store.conn.execute(
        """
        SELECT business_id, COUNT(DISTINCT value) AS variants
        FROM facts WHERE field = 'street'
        GROUP BY business_id HAVING variants > 1
        ORDER BY variants DESC LIMIT ?
        """,
        (limit,),
    ).fetchall()

    out: list[SuspectMerge] = []
    for row in rows:
        biz = store.get_business(row["business_id"])
        if biz is None:  # pragma: no cover — карточка удалена между запросами
            continue
        addresses = [
            r["value"]
            for r in store.conn.execute(
                "SELECT DISTINCT value FROM facts WHERE business_id = ? AND field = 'street'",
                (biz.id,),
            )
        ]
        out.append(
            SuspectMerge(
                business_id=biz.id,
                name=biz.name,
                addresses=addresses,
                sources=sorted(biz.refs),
            )
        )

    if only_different_streets:
        out = [s for s in out if s.different_streets]
    out.sort(key=lambda s: (not s.different_streets, -len(s.streets), s.name))
    return out
