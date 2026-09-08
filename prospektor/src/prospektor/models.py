"""Модель данных.

Три уровня, а не одна плоская таблица:

* :class:`Fact` — сырое утверждение источника о поле («телефон = +48…, по данным OSM»).
  Один и тот же телефон приходит из карты, с сайта и из реестра — все версии хранятся,
  конфликт остаётся видимым.
* :class:`Business` — материализованная проекция: по каждому полю берётся факт
  с наивысшим доверием к источнику.
* :class:`Signal` — результат пробы («меню только картинкой»), всегда с :class:`Evidence`.

Правило, которое держит всю конструкцию: любая цифра в выдаче прослеживается
до сигнала, любой сигнал — до evidence (URL + время + хэш куска HTML).
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


def utcnow() -> datetime:
    return datetime.now(UTC)


class LegalMode(StrEnum):
    """Правовой режим источника.

    ``CLEAN`` — лицензия или официальный API прямо разрешают использование.
    ``GRAY`` — сбор технически возможен, но нарушает ToS площадки (риск договорный:
    блокировки и cease-and-desist, а не уголовный). Включается только явным флагом.
    """

    CLEAN = "clean"
    GRAY = "gray"


class SignalKind(StrEnum):
    BOOL = "bool"
    NUM = "num"
    JSON = "json"


class Evidence(BaseModel):
    """Чем подтверждается сигнал. Без этого сигнал не имеет права попасть в базу."""

    model_config = ConfigDict(frozen=True)

    url: str
    fetched_at: datetime = Field(default_factory=utcnow)
    snippet_sha: str | None = None
    note: str | None = None

    @staticmethod
    def sha(snippet: str | bytes) -> str:
        raw = snippet.encode("utf-8") if isinstance(snippet, str) else snippet
        return hashlib.sha256(raw).hexdigest()[:16]


class Signal(BaseModel):
    """Одно измерение по бизнесу: ключ вида ``aio.menu_is_image_only`` и значение."""

    key: str
    kind: SignalKind
    value_bool: bool | None = None
    value_num: float | None = None
    value_json: Any = None
    evidence: Evidence | None = None
    checked_at: datetime = Field(default_factory=utcnow)

    @classmethod
    def flag(cls, key: str, value: bool, evidence: Evidence | None = None) -> Signal:
        return cls(key=key, kind=SignalKind.BOOL, value_bool=value, evidence=evidence)

    @classmethod
    def number(cls, key: str, value: float, evidence: Evidence | None = None) -> Signal:
        return cls(key=key, kind=SignalKind.NUM, value_num=value, evidence=evidence)

    @classmethod
    def data(cls, key: str, value: Any, evidence: Evidence | None = None) -> Signal:
        return cls(key=key, kind=SignalKind.JSON, value_json=value, evidence=evidence)

    @property
    def value(self) -> Any:
        if self.kind is SignalKind.BOOL:
            return self.value_bool
        if self.kind is SignalKind.NUM:
            return self.value_num
        return self.value_json


class Fact(BaseModel):
    """Утверждение конкретного источника о конкретном поле бизнеса."""

    field: str
    value: str
    source: str
    source_url: str | None = None
    confidence: float = 0.5
    fetched_at: datetime = Field(default_factory=utcnow)
    # Поля, помеченные как персональные данные, вырезаются экспортом ``--no-pii``.
    # Для JDG контактные данные — это персональные данные владельца, а не «данные фирмы».
    personal_data: bool = False


class Business(BaseModel):
    """Материализованная карточка. Собирается из фактов, руками не заполняется."""

    id: str
    name: str
    country: str = "PL"
    city: str | None = None
    postal_code: str | None = None
    street: str | None = None
    lat: float | None = None
    lon: float | None = None
    phone: str | None = None
    email: str | None = None
    website: str | None = None
    categories: list[str] = Field(default_factory=list)
    cuisines: list[str] = Field(default_factory=list)
    # Внешние идентификаторы: osm:node/123, overture:08f..., google:ChIJ..., nip:1234567890
    refs: dict[str, str] = Field(default_factory=dict)
    rating: float | None = None
    reviews_count: int | None = None
    first_seen: datetime = Field(default_factory=utcnow)
    last_seen: datetime = Field(default_factory=utcnow)


class RawRecord(BaseModel):
    """То, что вернул адаптер источника, до нормализации и дедупликации."""

    source: str
    source_url: str | None = None
    ref: str
    name: str
    facts: list[Fact] = Field(default_factory=list)
    lat: float | None = None
    lon: float | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class Run(BaseModel):
    """Манифест прогона. Пишет, чем именно собрано, — включая факт «серых» источников."""

    id: str
    stage: str
    area: str | None = None
    profile: str | None = None
    legal_mode: LegalMode = LegalMode.CLEAN
    sources_used: list[str] = Field(default_factory=list)
    cost_spent: float = 0.0
    started_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None
    notes: str | None = None
