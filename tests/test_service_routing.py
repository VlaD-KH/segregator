"""Раскладка документа в архив: имя, порядок операций, hardlink, два дерева.

Блокеры 5 и 8 (serene-honking-flurry.md, 2.5 и 2.8). Э5 построен Antigravity
(route/naming, route/linking, route/tree), но `service.py` его не звал —
`_route_to_archive` оставался тем же, чем был:

    doc_nr_clean = (facts.doc_number or "fv").replace("/", "_")   # только слэш
    shutil.copy2(src_file, target_path)                            # копия, не ссылка

Отсюда четыре наблюдаемых отказа, каждый молчаливый:

1. Номер фактуры приходит из документа. `FV\\12\\2026` уводил файл в
   несуществующий подкаталог, `FV:12` на NTFS не создавался вовсе — проводка
   в книге оставалась, бумаги под ней не было.
2. Два документа с одинаковым именем: `copy2` затирал первый без следа.
3. Коммит в БД шёл ДО раскладки (`service.py`: `_save_results_to_db`, затем
   `_route_to_archive`). Падение раскладки оставляло проводку без файла.
4. `documents.tree_path` и `link_path` (0001:53-54) не заполнялись никогда —
   узнать, куда лёг документ, можно было только обходом дерева.
"""

from __future__ import annotations

from datetime import date
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


def _facts(doc_number: str, *, doc_date: str = "2025-11-10", paid_date: str | None = None,
           seller: str = "PKN ORLEN S.A."):
    fields = {
        "nip_sprzedawcy": ExtractedField(value="5252344078", source=DataSource.OCR, confidence=0.98),
        "nazwa_sprzedawcy": ExtractedField(value=seller, source=DataSource.OCR, confidence=0.98),
        "nr_dokumentu": ExtractedField(value=doc_number, source=DataSource.OCR, confidence=0.98),
        "data_wystawienia": ExtractedField(value=doc_date, source=DataSource.OCR, confidence=0.98),
        "netto": ExtractedField(value=1000.0, source=DataSource.OCR, confidence=0.98),
        "vat": ExtractedField(value=230.0, source=DataSource.OCR, confidence=0.98),
        "brutto": ExtractedField(value=1230.0, source=DataSource.OCR, confidence=0.98),
    }
    if paid_date:
        fields["data_platnosci"] = ExtractedField(value=paid_date, source=DataSource.OCR, confidence=0.95)
    return DocumentFacts(doc_type=DocumentType.FAKTURA_KOSZTOWA, fields=fields, decision=AgentDecision.OK)


def _process(service, profile, tmp_path, name: str, facts: DocumentFacts, content: bytes = b"faktura"):
    doc = tmp_path / name
    doc.write_bytes(content)
    return service.process_document(doc, profile, custom_facts=facts)


def _archived(service):
    return [p for p in service.root.rglob("*") if p.is_file() and "archiwum" in p.parts]


# --- блокер 5: имя файла --------------------------------------------------------


@pytest.mark.parametrize("doc_number", ["FV\\12\\2026", "FV:12/2026", 'FV"12<2026>', "FV|12?2026*"])
def test_forbidden_characters_in_doc_number_do_not_lose_the_file(tmp_path, profile, doc_number):
    """Номер приходит из документа — то есть от постороннего. Раньше чистился
    только слэш, и такой номер оставлял проводку без бумаги."""
    service = SegregatorService(workspace_root=tmp_path)
    state = _process(service, profile, tmp_path, "f.txt", _facts(doc_number))

    assert state.status == "completed"
    assert _archived(service), f"документ с номером {doc_number!r} не доехал до архива"


def test_two_documents_with_the_same_name_do_not_overwrite_each_other(tmp_path, profile):
    """`shutil.copy2` затирал первый файл молча."""
    service = SegregatorService(workspace_root=tmp_path)
    _process(service, profile, tmp_path, "a.txt", _facts("FV/1"), content=b"pierwsza")
    _process(service, profile, tmp_path, "b.txt", _facts("FV/1"), content=b"druga")

    # Считаем по дереву документов: файл раскладывается в оба дерева, поэтому
    # два документа дают четыре ссылки.
    by_doc = [p for p in _archived(service) if "wg-daty-dokumentu" in p.parts]
    assert len(by_doc) == 2, "второй документ затёр первый"
    assert {p.read_bytes() for p in by_doc} == {b"pierwsza", b"druga"}
    assert any("__2" in p.name for p in by_doc), "коллизия разрешена не суффиксом"


# --- блокер 5: порядок операций и tree_path -------------------------------------


def test_tree_path_records_where_the_document_actually_landed(tmp_path, profile):
    """documents.tree_path существует с миграции 0001 и не заполнялся никогда."""
    service = SegregatorService(workspace_root=tmp_path)
    _process(service, profile, tmp_path, "f.txt", _facts("FV/1"))

    conn = sqlite3.connect(service.db_path)
    try:
        (tree_path,) = conn.execute("SELECT tree_path FROM documents").fetchone()
    finally:
        conn.close()

    assert tree_path, "tree_path пуст — куда лёг документ, знает только файловая система"
    from pathlib import Path
    assert Path(tree_path).exists(), "tree_path указывает в никуда"


def test_failed_archiving_leaves_no_orphan_entry(tmp_path, profile, monkeypatch):
    """Раскладка идёт ДО коммита в БД. Иначе падение копирования оставляет
    проводку в книге без бумаги под ней."""
    service = SegregatorService(workspace_root=tmp_path)

    from segregator.route import linking

    def explode(src, dst):
        raise OSError("диск переполнен")

    monkeypatch.setattr(linking, "link_or_copy", explode)
    monkeypatch.setattr("segregator.service.link_or_copy", explode, raising=False)

    with pytest.raises(OSError):
        _process(service, profile, tmp_path, "f.txt", _facts("FV/1"))

    conn = sqlite3.connect(service.db_path)
    try:
        booked = conn.execute("SELECT COUNT(*) FROM kpir_entries").fetchone()[0]
    finally:
        conn.close()
    assert booked == 0, "проводка осталась в книге, а файла под ней нет"


# --- блокер 8: hardlink ---------------------------------------------------------


def test_archive_uses_a_hardlink_not_a_copy(tmp_path, profile):
    """F-5.3. На диске свободно ~19 ГБ, архив по закону лежит 5 лет, и одни и
    те же байты получали до трёх мест: blob и два дерева."""
    service = SegregatorService(workspace_root=tmp_path)
    _process(service, profile, tmp_path, "f.txt", _facts("FV/1"))

    archived = _archived(service)
    assert archived

    from segregator.route.linking import same_physical_file
    blobs = [p for p in service.blobs_dir.rglob("*") if p.is_file()]
    assert blobs, "blob не создан — тест ничего не проверяет"
    assert any(same_physical_file(b, a) for b in blobs for a in archived), (
        "файл в архиве — копия, а не ссылка на blob"
    )


# --- F-5.2: второе дерево -------------------------------------------------------


def test_paid_document_also_lands_in_the_payment_tree(tmp_path, profile):
    """Дерево wg-daty-platnosci создавалось скелетом и не заполнялось никогда."""
    service = SegregatorService(workspace_root=tmp_path)
    _process(service, profile, tmp_path, "f.txt", _facts("FV/1", paid_date="2025-12-05"))

    by_payment = [p for p in _archived(service) if "wg-daty-platnosci" in p.parts]
    assert by_payment, "второе дерево пустое"
    assert any("12-grudzien" in p.parts for p in by_payment), "разложено не по дате платежа"


def test_unpaid_document_goes_to_the_dedicated_folder(tmp_path, profile):
    from segregator.paths import NO_PAYMENT_DATE_DIR

    service = SegregatorService(workspace_root=tmp_path)
    _process(service, profile, tmp_path, "f.txt", _facts("FV/1", paid_date=None))

    by_payment = [p for p in _archived(service) if "wg-daty-platnosci" in p.parts]
    assert by_payment, "неоплаченный документ во втором дереве не появился"
    assert any(NO_PAYMENT_DATE_DIR in p.parts for p in by_payment)


def test_both_trees_share_one_physical_file(tmp_path, profile):
    """Два дерева не должны удваивать место — на то и hardlink."""
    from segregator.route.linking import same_physical_file

    service = SegregatorService(workspace_root=tmp_path)
    _process(service, profile, tmp_path, "f.txt", _facts("FV/1", paid_date="2025-12-05"))

    by_doc = [p for p in _archived(service) if "wg-daty-dokumentu" in p.parts]
    by_pay = [p for p in _archived(service) if "wg-daty-platnosci" in p.parts]
    assert by_doc and by_pay
    assert same_physical_file(by_doc[0], by_pay[0]), "деревья держат две независимые копии"


# --- один источник имён месяцев -------------------------------------------------


def test_service_does_not_keep_its_own_month_names(tmp_path):
    """`MonthNames` в service.py дублировал route.tree.month_folder, и именно
    он молча отдавал `13-miesiac` вместо отказа."""
    import segregator.service as service_module

    assert not hasattr(service_module, "MonthNames"), (
        "имена месяцев должны жить в одном месте — route/tree.py"
    )
