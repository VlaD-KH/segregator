"""Настройки модуля.

Все пути принудительно удерживаются внутри ``prospektor/`` — это часть контракта
изоляции, а не удобство: модуль не должен уметь записать или прочитать что-либо
в рабочих каталогах основного проекта.
"""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MODULE_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PROSPEKTOR_", env_file=".env", extra="ignore"
    )

    data_dir: Path = Field(default=MODULE_ROOT / "data")
    export_dir: Path = Field(default=MODULE_ROOT / "exports")
    db_name: str = "prospektor.db"

    # Сеть
    user_agent: str = (
        "prospektor/0.1 (business-listing audit; contact: set PROSPEKTOR_USER_AGENT)"
    )
    request_timeout: float = 20.0
    concurrency: int = 8
    # Пауза между запросами к одному хосту. Вежливость здесь дешевле, чем бан.
    per_host_delay: float = 1.0
    cache_ttl_hours: int = 24 * 7

    # Ключи внешних API. Пусто — соответствующий адаптер просто не активируется.
    google_api_key: str | None = None
    pagespeed_api_key: str | None = None
    overpass_url: str = "https://overpass-api.de/api/interpreter"
    # Релиз Overture фиксируется явно: «последний» ломает воспроизводимость выборки.
    overture_release: str = "2026-08-20.0"

    # Бюджет платных источников на один прогон, в USD. Превышение — отказ, не трата.
    budget_usd: float = 0.0

    # «Серые» источники. Двойной замок: переменная окружения и явный флаг CLI.
    allow_gray: bool = False

    @field_validator("data_dir", "export_dir")
    @classmethod
    def _must_stay_inside_module(cls, value: Path) -> Path:
        resolved = Path(value).resolve()
        if not resolved.is_relative_to(MODULE_ROOT):
            raise ValueError(
                f"путь {resolved} выходит за пределы модуля {MODULE_ROOT}: "
                "prospektor не имеет права писать и читать вне своего каталога"
            )
        return resolved

    @property
    def db_path(self) -> Path:
        return self.data_dir / self.db_name


def load_settings(**overrides: object) -> Settings:
    settings = Settings(**overrides)  # type: ignore[arg-type]
    if os.environ.get("PROSPEKTOR_ALLOW_GRAY") == "1":
        settings.allow_gray = True
    return settings
