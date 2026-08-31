from typer.testing import CliRunner

from segregator import cli
from segregator.cli import app

runner = CliRunner()


def test_doctor_reports_missing_tesseract_without_crashing(isolated_project, monkeypatch):
    monkeypatch.setattr(cli.shutil, "which", lambda name: None)
    result = runner.invoke(app, ["doctor"])

    assert "[FAIL] tesseract: не найден в PATH" in result.output
    assert result.exit_code == 1


def test_doctor_reports_llm_unavailable_gracefully(isolated_project):
    # Порт 8080 на 127.0.0.1 не поднят в тестовом окружении — соединение
    # должно быть поймано и отражено в отчёте, а не привести к трассировке.
    # Ненулевой exit code здесь ожидаем: LLM — одна из проверок, которая
    # обязана пройти, чтобы doctor вернул 0.
    result = runner.invoke(app, ["doctor"])

    assert "недоступен" in result.output
    assert "Traceback" not in result.output


def test_doctor_all_checks_pass_exits_zero(isolated_project, monkeypatch):
    monkeypatch.setattr(cli, "_check_tesseract", lambda: (True, "tesseract", "/usr/bin/tesseract"))
    monkeypatch.setattr(cli, "_check_llm", lambda settings: (True, "LLM", "ok"))
    monkeypatch.setattr(cli, "_check_writable", lambda label, target: (True, label, str(target)))
    monkeypatch.setattr(cli, "_check_readable", lambda label, target: (True, label, str(target)))

    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0, result.output
