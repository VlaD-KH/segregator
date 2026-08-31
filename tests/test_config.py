import pytest
from pydantic import ValidationError

from segregator.config import Settings


def test_valid_settings_load(isolated_project):
    settings = Settings()
    assert settings.telegram_bot_token == "test-token"
    assert settings.owner_user_id == 123456
    assert settings.llm_backend == "llama.cpp"
    assert settings.export_dir == isolated_project["fake_export"]
    assert settings.thresholds.default == 0.85
    assert settings.tree.categories == ["koszty", "przychody"]


def test_missing_required_field_fails(isolated_project, monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    with pytest.raises(ValidationError):
        Settings()


def test_threshold_out_of_range_fails(isolated_project, tmp_path):
    (tmp_path / "config.toml").write_text(
        "[thresholds]\ndefault = 1.5\n", encoding="utf-8"
    )
    with pytest.raises(ValidationError):
        Settings()


def test_non_local_llm_base_url_rejected(isolated_project, monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "https://api.example.com/v1")
    with pytest.raises(ValidationError, match="localhost"):
        Settings()


def test_nonexistent_export_dir_rejected(isolated_project, monkeypatch, tmp_path):
    monkeypatch.setenv("EXPORT_DIR", str(tmp_path / "does-not-exist"))
    with pytest.raises(ValidationError):
        Settings()
