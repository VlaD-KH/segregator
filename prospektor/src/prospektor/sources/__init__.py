"""Реестр источников.

Адаптер попадает в прогон, только если (а) объявлен здесь, (б) доступен
(есть ключ/зависимость) и (в) его правовой режим разрешён настройками.
Третье условие — единственное место, где решается вопрос «серых» источников.
"""

from __future__ import annotations

from prospektor.config import Settings
from prospektor.models import LegalMode
from prospektor.sources.base import Area, BudgetExceeded, BudgetGovernor, SourceAdapter
from prospektor.sources.osm_overpass import OverpassSource
from prospektor.sources.overture import OvertureSource

# Приоритет источников при разрешении конфликтов фактов: чем выше число,
# тем больше доверия. Реестр важнее карты, карта важнее эвристики с сайта.
SOURCE_TRUST: dict[str, float] = {
    "ceidg": 0.95,
    "krs": 0.95,
    "regon": 0.95,
    "google": 0.85,
    "osm": 0.75,
    "site": 0.7,
    "overture": 0.6,
    "gray": 0.4,
}

_CLEAN: dict[str, type[SourceAdapter]] = {
    "osm": OverpassSource,
    "overture": OvertureSource,
}


def build_sources(
    settings: Settings,
    governor: BudgetGovernor,
    names: list[str] | None = None,
    allow_gray: bool = False,
) -> list[SourceAdapter]:
    registry: dict[str, type[SourceAdapter]] = dict(_CLEAN)
    if allow_gray:
        from prospektor.sources.gray import GRAY_SOURCES

        registry.update(GRAY_SOURCES)

    wanted = names or list(registry)
    built: list[SourceAdapter] = []
    for name in wanted:
        cls = registry.get(name)
        if cls is None:
            continue
        adapter = cls(settings, governor)
        if adapter.legal_mode is LegalMode.GRAY and not allow_gray:
            continue
        if adapter.available():
            built.append(adapter)
    return built


__all__ = [
    "SOURCE_TRUST",
    "Area",
    "BudgetExceeded",
    "BudgetGovernor",
    "SourceAdapter",
    "build_sources",
]
