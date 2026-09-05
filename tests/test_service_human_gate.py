"""Human Gate: карантин вместо проводки, реальный lp, аудит, повторная обработка.

Блокеры 2, 3, 6 (serene-honking-flurry.md, Часть 2). До этих правок
`_save_results_to_db` писал в `kpir_entries` по одному условию — «есть
kpir_entry» — и `status` не смотрела вовсе, хотя `agent02` заполняет
`kpir_entry` всегда, эскалация это или нет.
"""

from datetime import date
import json
import sqlite3

import pytest

from segregator.domain.models import (
    AgentDecision,
    DataSource,
    DocumentFacts,
    DocumentType,
    EmploymentPeriod,
    EmploymentTypeKind,
    ExtractedField,
    TaxRegime,
    TaxpayerProfile,
)
from segregator.service import SegregatorService

IBAN = "PL61109010140000071219812874"


@pytest.fixture
def profile():
    return TaxpayerProfile(
        pesel_masked="900101*****",
        nip="5252344078",
        date_of_birth=date(1990, 1, 1),
        jdg_tax_regime=TaxRegime.SKALA,
        employment_history=[
            EmploymentPeriod(emp_type=EmploymentTypeKind.JDG, start_date=date(2025, 10, 1))
        ],
    )


def _valid_facts(doc_number: str, doc_date: str = "2025-11-10", **extra_fields):
    fields = {
        "nip_sprzedawcy": ExtractedField(value="5252344078", source=DataSource.OCR, confidence=0.98),
        "nazwa_sprzedawcy": ExtractedField(value="PKN ORLEN S.A.", source=DataSource.OCR, confidence=0.98),
        "nr_dokumentu": ExtractedField(value=doc_number, source=DataSource.OCR, confidence=0.98),
        "data_wystawienia": ExtractedField(value=doc_date, source=DataSource.OCR, confidence=0.98),
        "netto": ExtractedField(value=1000.0, source=DataSource.OCR, confidence=0.98),
        "vat": ExtractedField(value=230.0, source=DataSource.OCR, confidence=0.98),
        "brutto": ExtractedField(value=1230.0, source=DataSource.OCR, confidence=0.98),
    }
    for name, value in extra_fields.items():
        fields[name] = ExtractedField(value=value, source=DataSource.OCR, confidence=0.95)
    return DocumentFacts(doc_type=DocumentType.FAKTURA_KOSZTOWA, fields=fields, decision=AgentDecision.OK)


def _escalating_facts(doc_number: str, **extra_fields):
    """Netto + VAT != Brutto — тот же приём, что в test_orchestrator_graph.py."""
    fields = {
        "nip_sprzedawcy": ExtractedField(value="5252344078", source=DataSource.OCR, confidence=0.90),
        "nazwa_sprzedawcy": ExtractedField(value="PKN ORLEN S.A.", source=DataSource.OCR, confidence=0.90),
        "nr_dokumentu": ExtractedField(value=doc_number, source=DataSource.OCR, confidence=0.90),
        "data_wystawienia": ExtractedField(value="2025-11-10", source=DataSource.OCR, confidence=0.90),
        "netto": ExtractedField(value=1000.0, source=DataSource.OCR, confidence=0.90),
        "vat": ExtractedField(value=230.0, source=DataSource.OCR, confidence=0.90),
        "brutto": ExtractedField(value=1500.0, source=DataSource.OCR, confidence=0.90),  # ошибка
    }
    for name, value in extra_fields.items():
        fields[name] = ExtractedField(value=value, source=DataSource.OCR, confidence=0.95)
    return DocumentFacts(doc_type=DocumentType.FAKTURA_KOSZTOWA, fields=fields, decision=AgentDecision.OK)


def _conn(service):
    return sqlite3.connect(service.db_path)


# --- эскалированный документ не проводится -------------------------------------


def test_escalated_document_not_written_to_kpir_entries(tmp_path, profile):
    service = SegregatorService(workspace_root=tmp_path)
    doc = tmp_path / "f1.txt"
    doc.write_text("faktura 1", encoding="utf-8")

    state = service.process_document(doc, profile, custom_facts=_escalating_facts("FV/1"))

    assert state.status == "escalated_to_human"
    conn = _conn(service)
    try:
        assert conn.execute("SELECT COUNT(*) FROM kpir_entries").fetchone()[0] == 0
    finally:
        conn.close()


def test_escalated_document_lands_in_quarantine_with_reason(tmp_path, profile):
    service = SegregatorService(workspace_root=tmp_path)
    doc = tmp_path / "f1.txt"
    doc.write_text("faktura 1", encoding="utf-8")

    state = service.process_document(doc, profile, custom_facts=_escalating_facts("FV/1"))

    conn = _conn(service)
    try:
        row = conn.execute(
            "SELECT lp, doc_number, escalation_reason FROM kpir_quarantine"
        ).fetchone()
    finally:
        conn.close()

    assert row is not None, "эскалированный документ должен лечь в kpir_quarantine"
    lp, doc_number, reason = row
    assert lp is None, "номер KPiR не выдаётся непроведённому документу"
    assert doc_number == "FV/1"
    assert reason == state.escalation_reason
    assert "Расхождение" in reason


def test_quarantine_masks_account_number_in_raw_facts_json(tmp_path, profile):
    """Тот же контур, что и kpir_entries (test_masking.py) — теперь для карантина."""
    service = SegregatorService(workspace_root=tmp_path)
    doc = tmp_path / "f1.txt"
    doc.write_text("faktura 1", encoding="utf-8")

    service.process_document(doc, profile, custom_facts=_escalating_facts("FV/1", numer_konta=IBAN))

    conn = _conn(service)
    try:
        (raw,) = conn.execute("SELECT raw_facts_json FROM kpir_quarantine").fetchone()
    finally:
        conn.close()

    assert IBAN not in raw
    assert json.loads(raw)["fields"]["numer_konta"]["value"] == "PL**...2874"


def test_escalated_document_is_archived_anyway(tmp_path, profile):
    """Решение владельца: документ не теряется — карантин в БД, файл в архив."""
    service = SegregatorService(workspace_root=tmp_path)
    doc = tmp_path / "f1.txt"
    doc.write_text("faktura 1", encoding="utf-8")

    service.process_document(doc, profile, custom_facts=_escalating_facts("FV/1"))

    archived = list(service.archive_dir.rglob("*.txt"))
    assert archived, "эскалированный документ должен попасть в archiwum/"


def test_escalated_document_does_not_write_zus_or_tax_advances(tmp_path, profile):
    """agent03 считает ZUS/PIT ещё до шлюза — но записи в ленту цифр без
    подтверждения человеком попадать не должны."""
    service = SegregatorService(workspace_root=tmp_path)
    doc = tmp_path / "f1.txt"
    doc.write_text("faktura 1", encoding="utf-8")

    state = service.process_document(doc, profile, custom_facts=_escalating_facts("FV/1"))

    assert state.zus_obligations is not None, "agent03 обязан был отработать до шлюза"
    conn = _conn(service)
    try:
        assert conn.execute("SELECT COUNT(*) FROM zus_declarations").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM tax_advances").fetchone()[0] == 0
    finally:
        conn.close()


def test_escalated_document_is_reprocessed_not_skipped(tmp_path, profile):
    """Повторная обработка того же документа не падает (idempotent `documents`,
    блокер 6) и не превращается в skipped_idle — карантинный документ не
    входит в join get_sync_state, значит остаётся дельтой."""
    service = SegregatorService(workspace_root=tmp_path)
    doc = tmp_path / "f1.txt"
    doc.write_text("faktura 1", encoding="utf-8")
    facts = _escalating_facts("FV/1")

    first = service.process_document(doc, profile, custom_facts=facts)
    second = service.process_document(doc, profile, custom_facts=facts)

    assert first.status == second.status == "escalated_to_human"
    assert second.is_delta_empty is False, "карантинный документ не должен считаться синхронизированным"

    conn = _conn(service)
    try:
        assert conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM kpir_quarantine").fetchone()[0] == 2
    finally:
        conn.close()


# --- реальный lp (блокер 3) -----------------------------------------------------


def test_lp_is_sequential_across_documents_in_the_same_year(tmp_path, profile):
    service = SegregatorService(workspace_root=tmp_path)

    doc1 = tmp_path / "f1.txt"
    doc1.write_text("faktura 1", encoding="utf-8")
    doc2 = tmp_path / "f2.txt"
    doc2.write_text("faktura 2", encoding="utf-8")

    state1 = service.process_document(doc1, profile, custom_facts=_valid_facts("FV/1", "2025-11-05"))
    state2 = service.process_document(doc2, profile, custom_facts=_valid_facts("FV/2", "2025-11-20"))

    assert state1.status == state2.status == "completed"
    conn = _conn(service)
    try:
        rows = conn.execute("SELECT doc_number, lp FROM kpir_entries ORDER BY lp").fetchall()
    finally:
        conn.close()

    assert rows == [("FV/1", 1), ("FV/2", 2)], "lp обязан расти, а не оставаться константой"


# --- аудит-след сохраняется (AuditEntry) ----------------------------------------


def test_audit_trail_is_persisted_for_completed_document(tmp_path, profile):
    service = SegregatorService(workspace_root=tmp_path)
    doc = tmp_path / "f1.txt"
    doc.write_text("faktura 1", encoding="utf-8")

    state = service.process_document(doc, profile, custom_facts=_valid_facts("FV/1"))

    conn = _conn(service)
    try:
        rows = conn.execute(
            "SELECT node_name, action FROM audit_trail ORDER BY id"
        ).fetchall()
    finally:
        conn.close()

    assert len(rows) == len(state.audit_trail), (
        "AuditEntry дописывался в семи узлах и не сохранялся никуда — "
        "число строк в БД обязано совпасть с state.audit_trail"
    )
    assert any(action == "BOOKED_TO_KPIR" for _, action in rows)


def test_audit_trail_is_persisted_for_escalated_document(tmp_path, profile):
    """Аудит эскалации — тот случай, ради которого таблица и заводилась."""
    service = SegregatorService(workspace_root=tmp_path)
    doc = tmp_path / "f1.txt"
    doc.write_text("faktura 1", encoding="utf-8")

    service.process_document(doc, profile, custom_facts=_escalating_facts("FV/1"))

    conn = _conn(service)
    try:
        rows = conn.execute("SELECT node_name FROM audit_trail WHERE node_name = 'Human_Gate'").fetchall()
    finally:
        conn.close()
    assert rows, "эскалация обязана оставить след в audit_trail"
