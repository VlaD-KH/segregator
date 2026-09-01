"""
tests/test_service_and_cli.py
Тесты для сервисного слоя SegregatorService и CLI-команд ввода/вывода.
"""

from datetime import date
from decimal import Decimal
from pathlib import Path
import pytest
from typer.testing import CliRunner

from src.segregator.cli import app
from src.segregator.domain.models import (
    DataSource,
    DocumentFacts,
    ExtractedField,
    TaxpayerProfile,
    EmploymentPeriod,
    EmploymentType,
    TaxRegime,
)
from src.segregator.service import SegregatorService


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
                emp_type=EmploymentType.JDG,
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
        doc_type="faktura",
        seller_nip=ExtractedField(value="5252344078", source=DataSource.OCR),
        seller_name=ExtractedField(value="PKN ORLEN S.A.", source=DataSource.OCR),
        doc_number=ExtractedField(value="FV/2025/11/100", source=DataSource.OCR),
        doc_date=ExtractedField(value=date(2025, 11, 10), source=DataSource.OCR),
        netto=ExtractedField(value=Decimal('1000.00'), source=DataSource.OCR),
        vat=ExtractedField(value=Decimal('230.00'), source=DataSource.OCR),
        brutto=ExtractedField(value=Decimal('1230.00'), source=DataSource.OCR)
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


def test_cli_demo_run():
    runner = CliRunner()
    result = runner.invoke(app, ["demo-run"])
    assert result.exit_code == 0
    assert "SEGREGATOR: ДЕМОНСТРАЦИЯ МУЛЬТИАГЕНТНОГО КОНВЕЙЕРА" in result.output
    assert "ДЕМОНСТРАЦИОННЫЙ ПРОГОН УСПЕШНО ЗАВЕРШЕН" in result.output
