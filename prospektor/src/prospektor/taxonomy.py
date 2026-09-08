"""Загрузка словарей категорий, кухонь и каталога PKD."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from prospektor.config import MODULE_ROOT

TAXONOMY_DIR = MODULE_ROOT / "taxonomy"


def _load(name: str) -> dict[str, Any]:
    path = Path(TAXONOMY_DIR / name)
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


@lru_cache(maxsize=1)
def categories() -> dict[str, dict[str, Any]]:
    return _load("category_map.yaml").get("categories", {})


@lru_cache(maxsize=1)
def category_sets() -> dict[str, list[str]]:
    return _load("category_map.yaml").get("sets", {})


@lru_cache(maxsize=1)
def cuisines() -> dict[str, list[str]]:
    return _load("cuisines.yaml").get("cuisines", {})


@lru_cache(maxsize=1)
def pkd_sections() -> dict[str, Any]:
    return _load("pkd_pl.yaml").get("sections", {})


def resolve_categories(names: list[str]) -> list[str]:
    """Развернуть имена наборов и синонимы в канонические категории."""
    known = categories()
    sets = category_sets()
    alias_index = {
        alias.lower(): key
        for key, spec in known.items()
        for alias in [key, *spec.get("aliases", [])]
    }
    out: list[str] = []
    for name in names:
        low = name.strip().lower()
        if low in sets:
            out.extend(sets[low])
        elif low in alias_index:
            out.append(alias_index[low])
        else:
            out.append(low)
    # dict.fromkeys — дедупликация с сохранением порядка
    return list(dict.fromkeys(out))


def osm_filters(category: str) -> list[str]:
    return list(categories().get(category, {}).get("osm", []))


def source_values(category: str) -> set[str]:
    """Значения источников, соответствующие канонической категории.

    В карточке лежат сырые значения Overture (`beauty_salon`, `spas`), а профили
    и фильтры оперируют каноническими именами (`kosmetyczka`). Без этого
    отображения отбор кандидатов молча выбрасывал бы всех, у кого имя категории
    в источнике не совпало с нашим случайно.
    """
    spec = categories().get(category, {})
    values = {category, *(a.lower() for a in spec.get("aliases", []))}
    values.update(v.lower() for v in spec.get("overture", []))
    # Значения тегов OSM извлекаются из тех же фильтров, по которым идёт поиск:
    # «shop=hairdresser» и «amenity=restaurant][cuisine~pizza» дают hairdresser
    # и restaurant. Отдельный список не нужен и разъезжался бы с фильтрами.
    for flt in spec.get("osm", []):
        head = flt.split("]")[0]
        if "=" in head:
            values.add(head.split("=", 1)[1].strip().lower())
    return values


def expand_to_source_values(names: list[str]) -> set[str]:
    """Канонические категории и наборы -> все значения, по которым их узнают."""
    out: set[str] = set()
    for category in resolve_categories(names):
        out |= source_values(category)
    return out


def normalize_cuisine(raw: str | None) -> list[str]:
    """Значение тега ``cuisine`` OSM -> наши ключи кухонь.

    В OSM это свободный список через точку с запятой: ``sushi;japanese;asian``.
    """
    if not raw:
        return []
    tokens = {t.strip().lower() for part in raw.split(";") for t in part.split(",")}
    out = [key for key, values in cuisines().items() if tokens & set(values)]
    return sorted(out)
