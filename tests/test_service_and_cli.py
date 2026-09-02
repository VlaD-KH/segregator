"""
tests/test_service_and_cli.py
Тесты для сервисного слоя SegregatorService и CLI-команд ввода/вывода.
"""

from datetime import date
from decimal import Decimal
from pathlib import Path
import pytest
from typer.testing import CliRunner

from segregator.cli import app
from segregator.domain.models import (
    DataSource,
    DocumentType,
    AgentDecision,
    DocumentFacts,
    ExtractedField,
    TaxpayerProfile,
    EmploymentPeriod,
    EmploymentTypeKind,
    TaxRegime,
)
from segregator.service import SegregatorService


@pytest.fixture
def service_test_env(tmp_path):
    """Изолированное тестовое окружение с временной БД и папками."""
    service = SegregatorService(workspace_root=tmp_path)
    profile = TaxpayerProfile(
        pesel_masked="900101*****",
        nip="5252344078",
        date_of_birth=date(1990, 1, 1),
        jdg_tax_regime=TaxRegime.SKALA,
        employment_history=[
            EmploymentPeriod(
                emp_type=EmploymentTypeKind.JDG,
                start_date=date(2025, 10, 1)
            )
        ]
    )
    return service, profile, tmp_path


def test_service_process_document_and_route(service_test_env):
    service, profile, root = service_test_env
    
    # 1. Создаем тестовую фактуру
    doc_file = root / "test_invoice.txt"
    doc_file.write_text("Test invoice content", encoding="utf-8")
    
    custom_facts = DocumentFacts(
        doc_type=DocumentType.FAKTURA_KOSZTOWA,
        fields={
            "nip_sprzedawcy": ExtractedField(value="5252344078", source=DataSource.OCR, confidence=0.98),
            "nazwa_sprzedawcy": ExtractedField(value="PKN ORLEN S.A.", source=DataSource.OCR, confidence=0.98),
            "nr_dokumentu": ExtractedField(value="FV/2025/11/100", source=DataSource.OCR, confidence=0.98),
            "data_wystawienia": ExtractedField(value="2025-11-10", source=DataSource.OCR, confidence=0.98),
            "netto": ExtractedField(value=1000.0, source=DataSource.OCR, confidence=0.98),
            "vat": ExtractedField(value=230.0, source=DataSource.OCR, confidence=0.98),
            "brutto": ExtractedField(value=1230.0, source=DataSource.OCR, confidence=0.98),
        },
        decision=AgentDecision.OK
    )

    # 2. Обработка через сервис
    state = service.process_document(doc_file, profile, custom_facts=custom_facts)
    
    assert state.status == "completed"
    assert state.kpir_entry is not None
    assert state.kpir_entry.col_13_pozostale_wydatki == Decimal('836.25')

    # 3. Проверка генерации XLSX реестра
    xlsx_file = service.generate_monthly_register(2025, 11)
    assert xlsx_file.exists()
    assert xlsx_file.stat().st_size > 0


def test_cli_demo_run(tmp_path, monkeypatch):
    """Тест demo-run с гарантированной изоляцией в tmp_path."""
    monkeypatch.setenv("SEGREGATOR_ARCHIVE_DIR", str(tmp_path))
    runner = CliRunner()
    result = runner.invoke(app, ["demo-run"])
    assert result.exit_code == 0
    assert "SEGREGATOR — ДЕМОНСТРАЦИОННЫЙ ПРОГОН" in result.output
    assert "ДЕМОНСТРАЦИОННЫЙ ПРОГОН УСПЕШНО ЗАВЕРШЕН" in result.output
