"""CLI-точки входа: `segregator init`, `segregator doctor`, `segregator backfill`, `segregator demo-run`, `segregator process`, `segregator report`."""

from __future__ import annotations

import platform
import shutil
import sys
import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path

import httpx
import typer
from pydantic import ValidationError

from segregator import logging as slog
from segregator import paths
from segregator.config import Settings, format_validation_error
from segregator.db import migrate
from segregator.domain.models import (
    DataSource,
    DocumentType,
    AgentDecision,
    DocumentFacts,
    ExtractedField,
    EmploymentPeriod,
    EmploymentTypeKind,
    TaxpayerProfile,
    TaxRegime,
)
from segregator.service import SegregatorService
from segregator.compliance.pit36 import PIT36Consolidator, IncomeSourceRecord, PITBAttachment


def _ensure_utf8_console() -> None:
    # Консольная кодовая страница Windows не гарантированно содержит
    # кириллицу (например, польская региональная локаль даёт cp1250) —
    # без этого русский вывод CLI падает с UnicodeEncodeError.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


_ensure_utf8_console()

app = typer.Typer(add_completion=False, help="Segregator — локальный бухгалтерский архиватор и ИИ-платформа")


def _load_settings_or_exit() -> Settings:
    try:
        return Settings()
    except ValidationError as error:
        typer.echo(format_validation_error(error))
        raise typer.Exit(code=1)


# Корень рабочего пространства — всегда settings.archive_dir. Отдельного
# переключателя SEGREGATOR_ARCHIVE_DIR больше нет: он двигал только часть путей,
# создавая два расходящихся рабочих пространства, и заводил второй механизм
# конфига рядом с ARCHIVE_DIR, которым уже пользуется conftest.isolated_project.


@app.command()
def init() -> None:
    """Создать базу данных и корневой скелет дерева архива."""
    settings = _load_settings_or_exit()
    slog.configure_logging(settings.archive_dir / "logs")
    log = slog.get_logger(__name__)

    applied = migrate.migrate(settings.archive_dir / "segregator.db")
    created = paths.ensure_tree(settings)

    typer.echo(f"База данных: применено новых миграций — {len(applied)}")
    typer.echo(f"Дерево папок: создано новых директорий — {len(created)}")
    log.info("init.completed", migrations_applied=len(applied), dirs_created=len(created))


@app.command()
def backfill(
    dry_run: bool = typer.Option(False, "--dry-run", help="Посчитать, но ничего не записывать"),
    limit: int | None = typer.Option(None, "--limit", help="Разобрать только первые N сообщений"),
) -> None:
    """Разобрать исторический экспорт Telegram Desktop в архив."""
    from segregator.ingest.export_reader import ExportPathError
    from segregator.ingest.normalize import backfill as run_backfill

    settings = _load_settings_or_exit()
    slog.configure_logging(settings.archive_dir / "logs")

    try:
        stats = run_backfill(
            settings.archive_dir,
            settings.export_dir,
            dry_run=dry_run,
            limit=limit,
        )
    except FileNotFoundError as error:
        typer.echo(f"Экспорт не найден: {error}")
        raise typer.Exit(code=1)
    except ExportPathError as error:
        # Путь из данных увёл за пределы экспорта — это не «плохой файл»,
        # а повод остановиться и посмотреть, что за экспорт нам подсунули.
        typer.echo(f"Экспорт отклонён: {error}")
        raise typer.Exit(code=1)

    typer.echo(f"Сообщений:  добавлено {stats.messages_added}, уже было {stats.messages_skipped}")
    typer.echo(f"Вложений:   добавлено {stats.attachments_added}, уже было {stats.attachments_skipped}")
    typer.echo(f"Блобов:     новых {stats.blobs_new}, дублей {stats.blobs_deduped}")
    if stats.missing_files:
        typer.echo(f"Пропущено:  {stats.missing_files} вложений не найдено на диске")
    if dry_run:
        typer.echo("Сухой прогон — ничего не записано.")


def _check_tesseract() -> tuple[bool, str, str]:
    path = shutil.which("tesseract")
    if path:
        return True, "tesseract", path
    return False, "tesseract", "не найден в PATH"


def _check_llm(settings: Settings) -> tuple[bool, str, str]:
    url = f"{settings.llm_base_url.rstrip('/')}/models"
    try:
        response = httpx.get(url, timeout=2.0)
        response.raise_for_status()
        return True, "LLM", f"{settings.llm_base_url} отвечает"
    except (httpx.ConnectError, httpx.TimeoutException):
        return False, "LLM", f"{settings.llm_base_url} недоступен (сервер не запущен?)"
    except httpx.HTTPStatusError as error:
        return False, "LLM", f"{settings.llm_base_url} вернул {error.response.status_code}"


def _check_writable(label: str, target: object) -> tuple[bool, str, str]:
    import os
    from pathlib import Path

    path = Path(target)
    check_path = path if path.exists() else path.parent
    if check_path.exists() and os.access(check_path, os.W_OK):
        return True, label, str(path)
    return False, label, f"{path} — нет прав на запись"


def _check_readable(label: str, target: object) -> tuple[bool, str, str]:
    from pathlib import Path

    path = Path(target)
    if path.is_dir():
        return True, label, str(path)
    return False, label, f"{path} — не найден или не папка"


@app.command()
def doctor() -> None:
    """Проверить окружение: tesseract, доступность LLM, права на папки."""
    settings = _load_settings_or_exit()

    checks = [
        _check_tesseract(),
        _check_llm(settings),
        _check_readable("EXPORT_DIR", settings.export_dir),
        _check_writable("ARCHIVE_DIR", settings.archive_dir),
    ]

    for ok, label, detail in checks:
        typer.echo(f"[{'OK' if ok else 'FAIL'}] {label}: {detail}")

    is_wsl = "microsoft" in platform.uname().release.lower()
    typer.echo(f"[INFO] WSL2: {'да' if is_wsl else 'нет — реальное дерево должно жить в WSL2 (ТЗ §02)'}")

    if not all(ok for ok, *_ in checks):
        raise typer.Exit(code=1)


@app.command(name="demo-run")
def demo_run() -> None:
    """
    Запустить демонстрационный прогон:
    - Обработка 3 входящих документов (продажи, расходы авто 75%, ошибка с Human Gate)
    - Генерация KPiR записей и расчет ZUS
    - Формирование официального XLSX реестра KPiR
    - Консолидация годового отчета PIT-36 за 2025 год
    """
    settings = _load_settings_or_exit()
    slog.configure_logging(settings.archive_dir / "logs")
    log = slog.get_logger("cli.demo_run")

    typer.secho("\n=======================================================", fg=typer.colors.CYAN, bold=True)
    typer.secho("   SEGREGATOR — ДЕМОНСТРАЦИОННЫЙ ПРОГОН СИСТЕМЫ       ", fg=typer.colors.WHITE, bg=typer.colors.BLUE, bold=True)
    typer.secho("=======================================================\n", fg=typer.colors.CYAN, bold=True)

    service = SegregatorService(workspace_root=settings.archive_dir)

    profile = TaxpayerProfile(
        pesel_masked="900101*****",
        nip="5252344078",
        full_name_masked="Jan Kowalski",
        date_of_birth=date(1990, 1, 1),
        is_vat_payer=True,
        jdg_tax_regime=TaxRegime.SKALA,
        employment_history=[
            EmploymentPeriod(
                emp_type=EmploymentTypeKind.JDG,
                start_date=date(2025, 10, 1),
                monthly_gross_avg=Decimal('15000.00'),
                payer_nip="5252344078"
            )
        ]
    )

    # Синтетические входные «фактуры» демо-прогона — во временный каталог ОС,
    # а не в архив. Пока они писались в settings.archive_dir, они оседали там,
    # куда указывает ARCHIVE_DIR: однажды это была папка Google Drive, и
    # заглушки FV_2025_11_*.pdf уехали оттуда в git.
    demo_tmp_dir = Path(tempfile.mkdtemp(prefix="segregator-demo-"))

    # 1. Документ 1: Фактура продаж (B2B IT Consulting)
    f1_path = demo_tmp_dir / "FV_2025_11_001_Sprzedaz.pdf"
    f1_path.write_text("Faktura Sprzedazy IT B2B: Netto 12000.00 PLN, VAT 2760.00 PLN", encoding="utf-8")
    f1_facts = DocumentFacts(
        doc_type=DocumentType.FAKTURA_SPRZEDAZY,
        fields={
            "nr_dokumentu": ExtractedField(value="FV/2025/11/001", source=DataSource.KSEF, confidence=1.0),
            "data_wystawienia": ExtractedField(value="2025-11-10", source=DataSource.KSEF, confidence=1.0),
            "nazwa_sprzedawcy": ExtractedField(value="Jan Kowalski IT", source=DataSource.KSEF, confidence=1.0),
            "nip_sprzedawcy": ExtractedField(value="5252344078", source=DataSource.KSEF, confidence=1.0),
            "netto": ExtractedField(value=12000.0, source=DataSource.KSEF, confidence=1.0),
            "vat": ExtractedField(value=2760.0, source=DataSource.KSEF, confidence=1.0),
            "brutto": ExtractedField(value=14760.0, source=DataSource.KSEF, confidence=1.0),
            "stawka_vat": ExtractedField(value=0.23, source=DataSource.KSEF, confidence=1.0),
            "waluta": ExtractedField(value="PLN", source=DataSource.KSEF, confidence=1.0)
        },
        decision=AgentDecision.OK
    )

    # 2. Документ 2: Чек/Фактура на топливо Orlen (смешанное авто 75% KUP / 50% VAT)
    f2_path = demo_tmp_dir / "FV_2025_11_042_Orlen_Paliwo.pdf"
    f2_path.write_text("PKN ORLEN Faktura Paliwo: Netto 1000.00 PLN, VAT 230.00 PLN, Brutto 1230.00 PLN", encoding="utf-8")
    f2_facts = DocumentFacts(
        doc_type=DocumentType.FAKTURA_KOSZTOWA,
        fields={
            "nr_dokumentu": ExtractedField(value="FV/ORLEN/2025/11/042", source=DataSource.OCR, confidence=0.98),
            "data_wystawienia": ExtractedField(value="2025-11-15", source=DataSource.OCR, confidence=0.98),
            "nazwa_sprzedawcy": ExtractedField(value="PKN ORLEN S.A.", source=DataSource.OCR, confidence=0.98),
            "netto": ExtractedField(value=1000.0, source=DataSource.OCR, confidence=0.98),
            "vat": ExtractedField(value=230.0, source=DataSource.OCR, confidence=0.98),
            "brutto": ExtractedField(value=1230.0, source=DataSource.OCR, confidence=0.98),
            "stawka_vat": ExtractedField(value=0.23, source=DataSource.OCR, confidence=0.98),
            "waluta": ExtractedField(value="PLN", source=DataSource.OCR, confidence=1.0)
        },
        decision=AgentDecision.OK
    )

    # 3. Документ 3: Фактура с математической ошибкой (Human-in-the-Loop тест)
    f3_path = demo_tmp_dir / "FV_2025_11_999_Blad_Matematyczny.pdf"
    f3_path.write_text("Faktura z bledem: Netto 1000.00 PLN, VAT 230.00 PLN, Brutto 1500.00 PLN (Błąd!)", encoding="utf-8")
    f3_facts = DocumentFacts(
        doc_type=DocumentType.FAKTURA_KOSZTOWA,
        fields={
            "nr_dokumentu": ExtractedField(value="FV/BLAD/999", source=DataSource.OCR, confidence=0.95),
            "data_wystawienia": ExtractedField(value="2025-11-20", source=DataSource.OCR, confidence=0.95),
            "nazwa_sprzedawcy": ExtractedField(value="Dostawca X", source=DataSource.OCR, confidence=0.95),
            "netto": ExtractedField(value=1000.0, source=DataSource.OCR, confidence=0.95),
            "vat": ExtractedField(value=230.0, source=DataSource.OCR, confidence=0.95),
            "brutto": ExtractedField(value=1500.0, source=DataSource.OCR, confidence=0.95),
            "stawka_vat": ExtractedField(value=0.23, source=DataSource.OCR, confidence=0.95),
            "waluta": ExtractedField(value="PLN", source=DataSource.OCR, confidence=1.0)
        },
        decision=AgentDecision.OK
    )

    docs_to_run = [
        ("Документ 1 (Продажи B2B)", f1_path, f1_facts),
        ("Документ 2 (Топливо Orlen 75%)", f2_path, f2_facts),
        ("Документ 3 (Ошибка в сумме)", f3_path, f3_facts),
    ]

    for label, path, facts in docs_to_run:
        typer.secho(f"▶ Обработка: {label}...", fg=typer.colors.YELLOW, bold=True)
        res_state = service.process_document(path, profile, custom_facts=facts)
        
        if res_state.status == "completed":
            typer.secho(f"  ✓ Статус: COMPLETED", fg=typer.colors.GREEN, bold=True)
            if res_state.proposal:
                typer.echo(f"  📋 KPiR: Колонка {res_state.proposal.kpir_column} | Категория: {res_state.proposal.category}")
                typer.echo(f"  🚗 Автомобиль: KUP={(res_state.proposal.pit_cost_ratio or 1.0)*100}%, VAT={(res_state.proposal.vat_deduction_ratio or 1.0)*100}%")
            if res_state.kpir_entry:
                typer.echo(f"  💰 Проводка в KPiR: Доход={res_state.kpir_entry.col_7_przychody} zł, Расход (KUP)={res_state.kpir_entry.col_14_razem_wydatki} zł")
            if res_state.zus_obligations:
                typer.echo(f"  🛡️ ZUS: {res_state.zus_obligations.stage.value} (Соцвзносы: {res_state.zus_obligations.total_spoleczne} zł, Zdrowotna: {res_state.zus_obligations.skladka_zdrowotna} zł)")
        elif res_state.status == "escalated_to_human":
            typer.secho(f"  ⚠️ Статус: ESCALATED TO HUMAN (Human-in-the-Loop)", fg=typer.colors.RED, bold=True)
            typer.echo(f"  🚨 Причина: {res_state.escalation_reason}")
            typer.echo(f"  📲 Карточка отправлена оператору для ручного подтверждения.")
        typer.echo("")

    # Генерация XLSX Реестра
    typer.secho("📊 Генерация ежемесячного реестра KPiR за 2025-11...", fg=typer.colors.CYAN, bold=True)
    xlsx_path = service.generate_monthly_register(2025, 11)
    typer.secho(f"  ✓ XLSX Реестр успешно создан: {xlsx_path}", fg=typer.colors.GREEN, bold=True)

    # Генерация Годового Консолидированного PIT-36
    typer.secho("\n📑 Консолидация годового отчета PIT-36 за 2025 год...", fg=typer.colors.CYAN, bold=True)
    pit36 = PIT36Consolidator.consolidate_year_2025(
        pesel_masked=profile.pesel_masked,
        nip=profile.nip,
        uop_income=IncomeSourceRecord(
            source_name="UoP", source_description="Praca (5 мес)",
            revenue_przychod=Decimal('50000.00'), tax_costs_kup=Decimal('1250.00'),
            income_dochod=Decimal('48750.00'), social_zus_deductible=Decimal('6855.00'),
            advances_paid=Decimal('3525.00')
        ),
        uz_income=IncomeSourceRecord(
            source_name="UZ", source_description="Zlecenie (4 мес)",
            revenue_przychod=Decimal('32000.00'), tax_costs_kup=Decimal('4876.80'),
            income_dochod=Decimal('27123.20'), social_zus_deductible=Decimal('4387.20'),
            advances_paid=Decimal('2940.00')
        ),
        jdg_pit_b=PITBAttachment(
            nip=profile.nip, business_name="Jan Kowalski IT",
            revenue=Decimal('45000.00'), costs=Decimal('9000.00'), income=Decimal('36000.00')
        ),
        jdg_advances_paid=Decimal('4320.00')
    )

    typer.echo(f"  • Совокупный доход (UoP + UZ + JDG): {pit36.total_income} zł")
    typer.echo(f"  • Совокупные социальные взносы к вычету: {pit36.total_social_zus_deduction} zł")
    typer.echo(f"  • Налоговая база PIT-36: {pit36.tax_base_rounded} zł")
    typer.echo(f"  • Налог начисленный (12%/32% + Kwota wolna 30k zł): {pit36.calculated_tax} zł")
    typer.echo(f"  • Всего уплачено авансов: {pit36.total_advances_paid} zł")
    if pit36.tax_to_pay > Decimal('0.00'):
        typer.secho(f"  ➔ Итог к доплате в Urząd Skarbowy: {pit36.tax_to_pay} zł", fg=typer.colors.YELLOW, bold=True)
    else:
        typer.secho(f"  ➔ Итог к возврату (Nadpłata): {pit36.tax_overpayment_refund} zł", fg=typer.colors.GREEN, bold=True)

    typer.secho("\n=======================================================", fg=typer.colors.CYAN, bold=True)
    typer.secho("  ДЕМОНСТРАЦИОННЫЙ ПРОГОН УСПЕШНО ЗАВЕРШЕН (100% OK)   ", fg=typer.colors.BLACK, bg=typer.colors.GREEN, bold=True)
    typer.secho("=======================================================\n", fg=typer.colors.CYAN, bold=True)


@app.command()
def process(
    file_path: Path = typer.Argument(..., help="Путь к файлу для обработки (PDF, XML, JPG, PNG)")
) -> None:
    """Обработать произвольный документ через мультиагентный конвейер Segregator."""
    if not file_path.exists():
        typer.secho(f"Файл {file_path} не найден!", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    settings = _load_settings_or_exit()
    service = SegregatorService(workspace_root=settings.archive_dir)

    profile = TaxpayerProfile(
        pesel_masked="900101*****",
        nip="5252344078",
        date_of_birth=date(1990, 1, 1),
        jdg_tax_regime=TaxRegime.SKALA,
        employment_history=[EmploymentPeriod(emp_type=EmploymentTypeKind.JDG, start_date=date(2025, 1, 1))]
    )

    typer.echo(f"Обработка документа: {file_path.name}...")
    res = service.process_document(file_path, profile)
    typer.secho(f"Результат: Статус {res.status}", fg=typer.colors.GREEN if res.status == "completed" else typer.colors.YELLOW)
    if res.kpir_entry:
        typer.echo(f"KPiR: Колонка {res.proposal.kpir_column if res.proposal else '-'}, Сумма: {res.kpir_entry.col_14_razem_wydatki or res.kpir_entry.col_7_przychody} zł")


@app.command()
def report(
    period: str = typer.Argument(..., help="Месяц в формате YYYY-MM (например 2025-11)")
) -> None:
    """Сгенерировать официальный XLSX-реестр KPiR за указанный месяц."""
    try:
        y, m = map(int, period.split("-"))
    except ValueError:
        typer.secho("Неверный формат периода. Используйте YYYY-MM (например 2025-11)", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    settings = _load_settings_or_exit()
    service = SegregatorService(workspace_root=settings.archive_dir)
    out_path = service.generate_monthly_register(y, m)
    typer.secho(f"Реестр успешно сгенерирован: {out_path}", fg=typer.colors.GREEN, bold=True)
