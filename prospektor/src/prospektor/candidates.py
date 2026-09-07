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
