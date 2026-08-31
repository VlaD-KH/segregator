"""Смоук-тест инвариантов docs/DATA_BOUNDARY.md для скелета Э1.

Полная проверка (нет HTTP-клиентов на пути обработки документа вне
allowlist, все фикстуры синтетические) появится вместе с реальным
пайплайном в Э2+. Здесь — то, что уже можно проверить на уровне
конфигурации и логирования.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import structlog
from pydantic import ValidationError

from segregator.config import Settings
from segregator.logging import Sensitive, configure_logging


def test_sensitive_wrapper_is_masked_in_log_output(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    configure_logging(tmp_path / "logs")
    log = structlog.get_logger("test")

    log.info("dokument.przetworzony", pesel=Sensitive("02070803628"))

    content = (tmp_path / "logs" / "segregator.ndjson").read_text(encoding="utf-8")
    assert "02070803628" not in content
    assert "***" in content


def test_denylisted_key_is_masked_even_unwrapped(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    configure_logging(tmp_path / "logs")
    log = structlog.get_logger("test")

    log.info("bank.wyciag", iban="PL61109010140000071219812874")

    content = (tmp_path / "logs" / "segregator.ndjson").read_text(encoding="utf-8")
    assert "PL61109010140000071219812874" not in content
    record = json.loads(content.strip().splitlines()[-1])
    assert record["iban"] == "***"


def test_settings_rejects_non_local_llm_endpoint(isolated_project, monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "https://cloud.example.com/v1")
    with pytest.raises(ValidationError):
        Settings()


def test_fixtures_are_all_marked_synthetic():
    fixtures_dir = Path(__file__).parent / "fixtures"
    manifest = json.loads((fixtures_dir / "manifest.json").read_text(encoding="utf-8"))

    document_files = [
        path
        for path in fixtures_dir.rglob("*")
        if path.is_file() and path.name not in {"manifest.json", "README.md"}
    ]
    for path in document_files:
        key = str(path.relative_to(fixtures_dir).as_posix())
        assert manifest.get(key, {}).get("synthetic") is True, (
            f"{key} не отмечен как synthetic в tests/fixtures/manifest.json"
        )
