"""Контракт TASK-003: hardlink вместо копии, граница тома, перенос ссылки.

Красные до появления `segregator.route.linking`. Тесты — задание, не черновик.

Почему это блокер: `_route_to_archive` (service.py:395) делает `shutil.copy2`.
На диске C: свободно ~19 ГБ, а одни и те же байты получают до трёх мест —
blob, дерево по дате документа, дерево по дате платежа. Решение записано
дважды (SPEC.md:187-189, F-5.3) и в коде не исполнено.

`link_or_copy` обязана звать именно `os.link` — тесты границы тома подменяют
его. Реализация через `Path.hardlink_to` контракту не соответствует.
"""

from __future__ import annotations

import errno
import os

import pytest

from segregator.route.linking import link_or_copy, relink, same_physical_file


@pytest.fixture
def blob(tmp_path):
    """Исходный файл, как он лежал бы в blobs/."""
    path = tmp_path / "blobs" / "fv.pdf"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"tresc faktury")
    return path


# --- link_or_copy -------------------------------------------------------------


def test_hardlink_is_the_default(blob, tmp_path):
    dst = tmp_path / "archiwum" / "fv.pdf"
    assert link_or_copy(blob, dst) == "hardlink"
    assert dst.read_bytes() == b"tresc faktury"


def test_hardlink_does_not_double_the_space(blob, tmp_path):
    """Проверка физическая: запись через один путь видна через другой."""
    dst = tmp_path / "archiwum" / "fv.pdf"
    link_or_copy(blob, dst)
    blob.write_bytes(b"po korekcie")
    assert dst.read_bytes() == b"po korekcie"
    assert same_physical_file(blob, dst)


def test_parent_directories_are_created(blob, tmp_path):
    dst = tmp_path / "archiwum" / "2026" / "09-wrzesien" / "koszty" / "fv.pdf"
    link_or_copy(blob, dst)
    assert dst.exists()


def test_existing_destination_is_never_clobbered(blob, tmp_path):
    dst = tmp_path / "archiwum" / "fv.pdf"
    dst.parent.mkdir(parents=True)
    dst.write_bytes(b"czyjs inny dokument")
    with pytest.raises(FileExistsError):
        link_or_copy(blob, dst)
    assert dst.read_bytes() == b"czyjs inny dokument"


def test_missing_source_fails_loudly(tmp_path):
    with pytest.raises(FileNotFoundError):
        link_or_copy(tmp_path / "nie-ma.pdf", tmp_path / "archiwum" / "fv.pdf")


def test_cross_volume_falls_back_to_copy(blob, tmp_path, monkeypatch):
    """F-5.4: через границу тома hardlink невозможен — ловится и объясняется."""
    def refuse(src, dst):
        raise OSError(errno.EXDEV, "Invalid cross-device link")

    monkeypatch.setattr(os, "link", refuse)
    dst = tmp_path / "archiwum" / "fv.pdf"
    assert link_or_copy(blob, dst) == "copy"
    assert dst.read_bytes() == b"tresc faktury"


def test_copy_fallback_is_a_real_copy(blob, tmp_path, monkeypatch):
    """Иначе «copy» соврало бы: подмена в blob не должна менять архив."""
    monkeypatch.setattr(os, "link", lambda src, dst: (_ for _ in ()).throw(
        OSError(errno.EXDEV, "Invalid cross-device link")))
    dst = tmp_path / "archiwum" / "fv.pdf"
    link_or_copy(blob, dst)
    blob.write_bytes(b"po korekcie")
    assert dst.read_bytes() == b"tresc faktury"
    assert not same_physical_file(blob, dst)


def test_other_os_errors_are_not_swallowed(blob, tmp_path, monkeypatch):
    """Ловится ровно EXDEV. Отказ в доступе, замаскированный под копию, —
    это молчаливая потеря дедупликации на всём архиве."""
    def refuse(src, dst):
        raise OSError(errno.EACCES, "Permission denied")

    monkeypatch.setattr(os, "link", refuse)
    with pytest.raises(OSError) as excinfo:
        link_or_copy(blob, tmp_path / "archiwum" / "fv.pdf")
    assert excinfo.value.errno == errno.EACCES


def test_cross_volume_warning_does_not_leak_the_document_name(blob, tmp_path, monkeypatch, caplog):
    """Контур важнее удобства отладки.

    Имя файла в архиве собрано из данных документа —
    `2025-11-10__koszty__orlen__FV_2025_11_100.pdf` несёт и контрагента, и
    номер фактуры. Инвариант 3 DATA_BOUNDARY.md не делает исключения для
    предупреждений: то же самое, за что заведён дефект D2, только там это
    были исключения, а здесь лог.
    """
    monkeypatch.setattr(os, "link", lambda src, dst: (_ for _ in ()).throw(
        OSError(errno.EXDEV, "Invalid cross-device link")))

    named = tmp_path / "blobs" / "2025-11-10__koszty__orlen__FV_2025_11_100.pdf"
    named.parent.mkdir(parents=True, exist_ok=True)
    named.write_bytes(b"tresc")

    with caplog.at_level("WARNING"):
        link_or_copy(named, tmp_path / "archiwum" / "2025-11-10__koszty__orlen__FV_2025_11_100.pdf")

    said = " ".join(r.message for r in caplog.records)
    assert "orlen" not in said.lower(), "имя контрагента ушло в лог"
    assert "FV_2025_11_100" not in said, "номер фактуры ушёл в лог"
    assert ".pdf" not in said, "имя файла документа ушло в лог"


def test_cross_volume_message_names_the_volumes(blob, tmp_path, monkeypatch, caplog):
    """«Объясняется» из F-5.4 — это внятная запись, а не тихий откат."""
    monkeypatch.setattr(os, "link", lambda src, dst: (_ for _ in ()).throw(
        OSError(errno.EXDEV, "Invalid cross-device link")))
    with caplog.at_level("WARNING"):
        link_or_copy(blob, tmp_path / "archiwum" / "fv.pdf")
    assert any("EXDEV" in r.message or "тома" in r.message for r in caplog.records)


# --- same_physical_file -------------------------------------------------------


def test_identical_bytes_are_not_the_same_file(blob, tmp_path):
    """Смысл функции — «это одна и та же ссылка», а не «содержимое совпало»."""
    twin = tmp_path / "twin.pdf"
    twin.write_bytes(blob.read_bytes())
    assert not same_physical_file(blob, twin)


def test_missing_path_is_false_not_an_error(blob, tmp_path):
    assert same_physical_file(blob, tmp_path / "nie-ma.pdf") is False


# --- relink -------------------------------------------------------------------


def test_relink_moves_the_link_without_making_a_second(blob, tmp_path):
    """F-5.6: переклассификация переносит ссылку, не плодит вторую."""
    old = tmp_path / "archiwum" / "koszty" / "fv.pdf"
    new = tmp_path / "archiwum" / "przychody" / "fv.pdf"
    link_or_copy(blob, old)

    relink(old, new)

    assert not old.exists()
    assert new.exists()
    assert same_physical_file(blob, new)


def test_relink_creates_parent_of_destination(blob, tmp_path):
    old = tmp_path / "archiwum" / "fv.pdf"
    link_or_copy(blob, old)
    new = tmp_path / "archiwum" / "2026" / "09-wrzesien" / "przychody" / "fv.pdf"
    relink(old, new)
    assert new.exists()


def test_relink_refuses_to_overwrite(blob, tmp_path):
    old = tmp_path / "archiwum" / "koszty" / "fv.pdf"
    new = tmp_path / "archiwum" / "przychody" / "fv.pdf"
    link_or_copy(blob, old)
    new.parent.mkdir(parents=True)
    new.write_bytes(b"czyjs inny dokument")

    with pytest.raises(FileExistsError):
        relink(old, new)
    assert old.exists(), "неудачный перенос не должен терять исходную ссылку"
    assert new.read_bytes() == b"czyjs inny dokument"
