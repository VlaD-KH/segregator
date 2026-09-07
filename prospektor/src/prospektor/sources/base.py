"""Контракт источника данных.

Каждый адаптер обязан объявить три вещи, и все три попадают в манифест прогона:
правовой режим, лимит скорости и стоимость вызова. Без этого невозможно ни
отвечать за законность выборки, ни удержать бюджет.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass

from prospektor.config import Settings
from prospektor.models import LegalMode, RawRecord


class BudgetExceeded(RuntimeError):
    """Прогон отказывается тратить сверх потолка. Отказ, а не молчаливая трата."""


@dataclass
class Area:
    """Территория поиска.

    Либо готовый bbox, либо название — тогда адаптер сам его геокодирует
    (OSM Nominatim; результат кэшируется, чтобы не долбить публичный сервис).
    """

    name: str
    bbox: tuple[float, float, float, float] | None = None  # (south, west, north, east)

    def require_bbox(self) -> tuple[float, float, float, float]:
        if self.bbox is None:
            raise ValueError(f"для области «{self.name}» не определён bbox")
        return self.bbox


class BudgetGovernor:
    """Считает потраченное и не даёт перешагнуть потолок.

    Проверка идёт ДО вызова: перерасход невозможен даже на один запрос.
    Потолок 0 означает «платные источники запрещены» — это и есть значение
    по умолчанию, чтобы случайный прогон не выставил счёт.
    """

    def __init__(self, limit_usd: float) -> None:
        self.limit = limit_usd
        self.spent = 0.0

    def charge(self, amount: float, what: str) -> None:
        if amount <= 0:
            return
        if self.spent + amount > self.limit:
            raise BudgetExceeded(
                f"{what}: потрачено ${self.spent:.4f}, запрос стоит ${amount:.4f}, "
                f"потолок ${self.limit:.4f}. Поднимите PROSPEKTOR_BUDGET_USD осознанно."
            )
        self.spent += amount


class SourceAdapter(ABC):
    """Базовый адаптер.

    ``discover`` отдаёт кандидатов по территории и категориям, ``details``
    дообогащает конкретную запись. Оба возвращают :class:`RawRecord` — сырьё,
    которое ещё предстоит нормализовать и сшить.
    """

    name: str = "base"
    legal_mode: LegalMode = LegalMode.CLEAN
    cost_per_call: float = 0.0
    # Требуемые настройки; если хоть одной нет — адаптер молча не активируется.
    requires: tuple[str, ...] = ()

    def __init__(self, settings: Settings, governor: BudgetGovernor | None = None) -> None:
        self.settings = settings
        self.governor = governor or BudgetGovernor(0.0)

    def available(self) -> bool:
        return all(getattr(self.settings, key, None) for key in self.requires)

    @abstractmethod
    def discover(self, area: Area, categories: list[str]) -> AsyncIterator[RawRecord]:
        """Найти кандидатов на территории."""

    async def details(self, record: RawRecord) -> RawRecord:
        """Дообогатить запись. По умолчанию — ничего не добавляет."""
        return record
