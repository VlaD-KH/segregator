"""Конфигурация: секреты и пути из .env/окружения, структурные настройки из config.toml.

Два источника не пересекаются по полям, поэтому порядок приоритета между
ними не имеет значения на практике — он зафиксирован в
``settings_customise_sources`` только чтобы pydantic-settings был
детерминирован.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


class FirmaConfig(BaseModel):
    nip: str | None = None  # проверка контрольной суммы — в Э3


class ThresholdsConfig(BaseModel):
    default: float = 0.85
    overrides: dict[str, float] = Field(default_factory=dict)

    @field_validator("default")
    @classmethod
    def _default_in_range(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError("thresholds.default должен быть в диапазоне [0, 1]")
        return v

    @field_validator("overrides")
    @classmethod
    def _overrides_in_range(cls, v: dict[str, float]) -> dict[str, float]:
        for category, threshold in v.items():
            if not 0.0 <= threshold <= 1.0:
                raise ValueError(
                    f"thresholds.overrides.{category} должен быть в диапазоне [0, 1]"
                )
        return v


class TreeConfig(BaseModel):
    years: list[int] = Field(default_factory=lambda: [2025])
    # Заглушка до Э0 — окончательный список категорий утверждается после
    # разведки реального содержимого канала (ТЗ §06, §13/Э0).
    categories: list[str] = Field(
        default_factory=lambda: [
            "przychody",
            "koszty",
            "zus",
            "podatki",
            "pit-11",
            "umowy",
            "bank",
            "do-wyjasnienia",
        ]
    )


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        toml_file="config.toml",
        extra="ignore",
        case_sensitive=False,
    )

    telegram_bot_token: str
    owner_user_id: int
    llm_backend: Literal["llama.cpp", "ollama"] = "llama.cpp"
    llm_base_url: str = "http://127.0.0.1:8080/v1"
    llm_model: str
    export_dir: Path
    archive_dir: Path
    ksef_enabled: bool = False

    firma: FirmaConfig = Field(default_factory=FirmaConfig)
    thresholds: ThresholdsConfig = Field(default_factory=ThresholdsConfig)
    tree: TreeConfig = Field(default_factory=TreeConfig)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            TomlConfigSettingsSource(settings_cls),
            file_secret_settings,
        )

    @field_validator("llm_base_url")
    @classmethod
    def _llm_base_url_is_local(cls, v: str) -> str:
        if urlparse(v).hostname not in LOCAL_HOSTS:
            raise ValueError(
                "LLM_BASE_URL обязан указывать на localhost — контур данных "
                "запрещает внешние LLM-API (docs/DATA_BOUNDARY.md, инвариант 2)"
            )
        return v

    @field_validator("export_dir")
    @classmethod
    def _export_dir_exists(cls, v: Path) -> Path:
        if not v.is_dir():
            raise ValueError(f"EXPORT_DIR не найден или не является папкой: {v}")
        return v

    @model_validator(mode="after")
    def _archive_dir_writable(self) -> "Settings":
        target = self.archive_dir if self.archive_dir.exists() else self.archive_dir.parent
        if not target.exists() or not os.access(target, os.W_OK):
            raise ValueError(f"Нет прав на запись рядом с ARCHIVE_DIR: {target}")
        return self


def load_settings() -> Settings:
    return Settings()


def format_validation_error(error: ValidationError) -> str:
    lines = ["Ошибка конфигурации:"]
    for err in error.errors():
        location = ".".join(str(part) for part in err["loc"])
        lines.append(f"  {location}: {err['msg']}")
    return "\n".join(lines)
