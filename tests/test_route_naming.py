"""Контракт TASK-002: имена файлов, безопасные для NTFS, и разрешение коллизий.

Красные до появления `segregator.route.naming`. Тесты — задание, не черновик.

Почему это блокер, а не косметика: `_route_to_archive` (service.py:390) чистит
из имени только `/`. Номер фактуры вида `FV\\12\\2026` уводит файл в
несуществующий подкаталог, `FV:12` на NTFS не создаётся вовсе, а совпадение имён
затирает уже разложенный документ молча — проводка в книге останется, бумаги
под ней не будет.
"""

from __future__ import annotations

import pytest

from segregator.route.naming import safe_filename, unique_path

FORBIDDEN = '<>:"|?*'


# --- safe_filename ------------------------------------------------------------


def test_slash_and_backslash_do_not_create_directories():
    assert safe_filename("FV/2026/09.pdf") == "FV_2026_09.pdf"
    assert safe_filename("FV\\2026\\09.pdf") == "FV_2026_09.pdf"


def test_every_forbidden_ntfs_character_is_replaced():
    assert safe_filename(f"faktura{FORBIDDEN}.pdf") == "faktura" + "_" * len(FORBIDDEN) + ".pdf"


def test_control_characters_are_replaced():
    assert safe_filename("faktura\x00\x1f.pdf") == "faktura__.pdf"


def test_polish_diacritics_survive():
    """Имена в архиве польские (F-5.5). Транслитерация здесь была бы порчей."""
    name = "zakup paliwa Kraków ąćęłńóśźż ĄĆĘŁŃÓŚŹŻ.pdf"
    assert safe_filename(name) == name


def test_trailing_dots_and_spaces_are_stripped():
    """Windows молча их отбрасывает — файл окажется не под тем именем, что в БД."""
    assert safe_filename("faktura. ") == "faktura"
    assert safe_filename("faktura   ") == "faktura"


@pytest.mark.parametrize("reserved", ["CON", "con", "PRN.pdf", "AUX", "NUL.txt", "COM1.pdf", "LPT9"])
def test_reserved_device_names_are_defused(reserved):
    """CON, PRN, COM1… — устройства, не файлы. Создать такой файл нельзя."""
    result = safe_filename(reserved)
    assert result != reserved
    assert result.startswith("_")


def test_reserved_name_as_part_of_longer_name_is_left_alone():
    """Запрещено само имя устройства, а не подстрока: CONTRAKT — обычное слово."""
    assert safe_filename("CONTRAKT.pdf") == "CONTRAKT.pdf"


def test_name_that_collapses_to_nothing_gets_a_fallback():
    assert safe_filename("") == "bez-nazwy"
    assert safe_filename("...") == "bez-nazwy"


def test_long_stem_is_truncated_and_extension_kept():
    """Предел пути NTFS 260 (F-5.5); режется основа, расширение остаётся."""
    result = safe_filename("a" * 200 + ".pdf")
    assert result == "a" * 120 + ".pdf"


def test_max_stem_is_adjustable():
    assert safe_filename("a" * 50 + ".pdf", max_stem=10) == "a" * 10 + ".pdf"


# --- unique_path --------------------------------------------------------------


def test_free_path_is_returned_unchanged(tmp_path):
    target = tmp_path / "faktura.pdf"
    assert unique_path(target) == target


def test_occupied_path_gets_a_suffix(tmp_path):
    target = tmp_path / "faktura.pdf"
    target.touch()
    assert unique_path(target) == tmp_path / "faktura__2.pdf"


def test_suffix_grows_until_free(tmp_path):
    (tmp_path / "faktura.pdf").touch()
    (tmp_path / "faktura__2.pdf").touch()
    assert unique_path(tmp_path / "faktura.pdf") == tmp_path / "faktura__3.pdf"


def test_extension_is_preserved_not_appended_to(tmp_path):
    """faktura__2.pdf, а не faktura.pdf__2 — иначе файл перестаёт открываться."""
    target = tmp_path / "faktura.pdf"
    target.touch()
    assert unique_path(target).suffix == ".pdf"


def test_name_without_extension_also_works(tmp_path):
    target = tmp_path / "faktura"
    target.touch()
    assert unique_path(target) == tmp_path / "faktura__2"


def test_giving_up_is_loud_not_silent(tmp_path):
    """Исчерпав предел, функция обязана упасть, а не вернуть занятый путь."""
    (tmp_path / "faktura.pdf").touch()
    (tmp_path / "faktura__2.pdf").touch()
    with pytest.raises(FileExistsError):
        unique_path(tmp_path / "faktura.pdf", limit=2)
