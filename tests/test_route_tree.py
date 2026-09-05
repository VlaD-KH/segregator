"""Контракт TASK-004: два дерева архива и польские имена месяцев.

Красные до появления `segregator.route.tree`. Тесты — задание, не черновик.

Почему это блокер: `paths.ensure_tree` создаёт `archiwum/wg-daty-dokumentu/` и
`archiwum/wg-daty-platnosci/`, а `_route_to_archive` (service.py:384) кладёт
файл в `ARCHIVE_DIR/{год}/{месяц}/{категория}` — мимо обоих. Скелет строится
пустым, документы ложатся рядом с ним, второе дерево не заполняется никогда
(F-5.1, F-5.2 оба ○).

Собирать путь должно одно место, а не каждый вызывающий по памяти.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from segregator.paths import NO_PAYMENT_DATE_DIR
from segregator.route.tree import document_tree_path, month_folder, payment_tree_path

ARCHIVE = Path("C:/archiwum-test") if Path("C:/").exists() else Path("/archiwum-test")

PL_MONTHS = [
    (1, "01-styczen"), (2, "02-luty"), (3, "03-marzec"), (4, "04-kwiecien"),
    (5, "05-maj"), (6, "06-czerwiec"), (7, "07-lipiec"), (8, "08-sierpien"),
    (9, "09-wrzesien"), (10, "10-pazdziernik"), (11, "11-listopad"), (12, "12-grudzien"),
]


# --- month_folder -------------------------------------------------------------


@pytest.mark.parametrize("number,folder", PL_MONTHS)
def test_month_folders_are_polish_and_sortable(number, folder):
    """Числовой префикс обязателен: без него ls даёт алфавитный порядок."""
    assert month_folder(number) == folder


@pytest.mark.parametrize("bad", [0, 13, -1])
def test_impossible_month_is_rejected(bad):
    """service.py отдавал `13-miesiac` молча. Такой папки быть не должно."""
    with pytest.raises(ValueError):
        month_folder(bad)


# --- document_tree_path -------------------------------------------------------


def test_document_path_lands_inside_the_document_tree():
    result = document_tree_path(ARCHIVE, date(2026, 9, 4), "koszty", "fv.pdf")
    assert result == ARCHIVE / "archiwum" / "wg-daty-dokumentu" / "2026" / "09-wrzesien" / "koszty" / "fv.pdf"


def test_document_path_is_not_the_bare_archive_root():
    """Ровно тот дефект, что сейчас в service.py: файл рядом со скелетом, не в нём."""
    result = document_tree_path(ARCHIVE, date(2026, 9, 4), "koszty", "fv.pdf")
    assert result.parent != ARCHIVE / "2026" / "09-wrzesien" / "koszty"


# --- payment_tree_path --------------------------------------------------------


def test_payment_path_lands_inside_the_payment_tree():
    result = payment_tree_path(ARCHIVE, date(2026, 10, 15), "koszty", "fv.pdf")
    assert result == ARCHIVE / "archiwum" / "wg-daty-platnosci" / "2026" / "10-pazdziernik" / "koszty" / "fv.pdf"


def test_unpaid_document_goes_to_the_dedicated_folder():
    """F-5.2. Неоплаченное — не «январь неизвестного года» и не корень дерева."""
    result = payment_tree_path(ARCHIVE, None, "koszty", "fv.pdf")
    assert result == ARCHIVE / "archiwum" / "wg-daty-platnosci" / NO_PAYMENT_DATE_DIR / "fv.pdf"


def test_payment_and_document_trees_never_collide():
    paid = payment_tree_path(ARCHIVE, date(2026, 9, 4), "koszty", "fv.pdf")
    doc = document_tree_path(ARCHIVE, date(2026, 9, 4), "koszty", "fv.pdf")
    assert paid != doc


# --- путь не выходит за пределы дерева ----------------------------------------


def test_document_number_cannot_escape_the_tree():
    """Имя приходит из документа, то есть от постороннего. `FV/1/2026` не
    должен превращаться в подкаталоги, а `..` — уводить выше архива."""
    result = document_tree_path(ARCHIVE, date(2026, 9, 4), "koszty", "FV/1/2026.pdf")
    assert result.name == "FV_1_2026.pdf"
    assert result.parent == ARCHIVE / "archiwum" / "wg-daty-dokumentu" / "2026" / "09-wrzesien" / "koszty"


def test_category_cannot_escape_the_tree():
    result = document_tree_path(ARCHIVE, date(2026, 9, 4), "../../../etc", "fv.pdf")
    assert ".." not in result.parts


@pytest.mark.parametrize(
    "builder", [document_tree_path, payment_tree_path], ids=["dokumentu", "platnosci"]
)
def test_both_trees_stay_under_the_archive(builder):
    result = builder(ARCHIVE, date(2026, 9, 4), "koszty/../..", "fv.pdf")
    assert ARCHIVE in result.parents
