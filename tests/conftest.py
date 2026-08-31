"""Общие фикстуры. Ни один тест не должен ссылаться на реальный EXPORT_DIR
или что-либо под Downloads — единственный источник путей для тестов ниже.
"""

from __future__ import annotations

from pathlib import Path

import pytest

CONFIG_TOML = """
[firma]
nip = "1234563218"

[thresholds]
default = 0.85

[tree]
years = [2025]
categories = ["koszty", "przychody"]
"""


@pytest.fixture
def isolated_project(tmp_path, monkeypatch):
    """Изолированный проект: рабочая папка, синтетический экспорт, .env, config.toml."""
    monkeypatch.chdir(tmp_path)

    fake_export = tmp_path / "fake_export"
    fake_export.mkdir()
    (fake_export / "result.json").write_text("{}", encoding="utf-8")

    archive_dir = tmp_path / "archive"

    (tmp_path / "config.toml").write_text(CONFIG_TOML, encoding="utf-8")

    env = {
        "TELEGRAM_BOT_TOKEN": "test-token",
        "OWNER_USER_ID": "123456",
        "LLM_BACKEND": "llama.cpp",
        "LLM_BASE_URL": "http://127.0.0.1:8080/v1",
        "LLM_MODEL": "gemma-3-4b-it-Q4_K_M",
        "EXPORT_DIR": str(fake_export),
        "ARCHIVE_DIR": str(archive_dir),
        "KSEF_ENABLED": "false",
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    return {
        "tmp_path": tmp_path,
        "fake_export": fake_export,
        "archive_dir": archive_dir,
        "env": env,
    }
