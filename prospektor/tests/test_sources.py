"""Источники: разбор ответов и бюджетный губернатор."""

from __future__ import annotations

import pytest

from prospektor.config import load_settings
from prospektor.sources import BudgetExceeded, BudgetGovernor
from prospektor.sources.osm_overpass import OverpassSource
from tests.conftest import load_json


def test_элемент_overpass_превращается_в_запись() -> None:
    payload = load_json("osm/overpass_kielce.json")
    записи = [OverpassSource.to_record(e) for e in payload["elements"]]
    заполненные = [r for r in записи if r is not None]

    # Третий элемент без названия — это объект на карте, а не бизнес
    assert len(заполненные) == 2

    wok = заполненные[0]
    поля = {f.field: f.value for f in wok.facts}
    assert поля["website"] == "https://wokandroll.example.pl"
    assert поля["phone"] == "+48 12 345 67 89"
    assert поля["street"] == "Piłsudskiego 15"
    assert поля["city"] == "Kielce"
    assert поля["osm_delivery"] == "yes"
    assert wok.lat == pytest.approx(50.8661)


def test_кухни_нормализуются_в_наши_ключи() -> None:
    payload = load_json("osm/overpass_kielce.json")
    wok = OverpassSource.to_record(payload["elements"][0])
    assert wok is not None
    кухни = {f.value for f in wok.facts if f.field == "cuisine"}
    assert кухни == {"asian", "japanese"}


def test_контактные_данные_помечаются_персональными() -> None:
    """Для JDG телефон владельца — персональные данные, а не «данные фирмы»."""
    payload = load_json("osm/overpass_kielce.json")
    wok = OverpassSource.to_record(payload["elements"][0])
    assert wok is not None
    телефон = next(f for f in wok.facts if f.field == "phone")
    сайт = next(f for f in wok.facts if f.field == "website")
    assert телефон.personal_data is True
    assert сайт.personal_data is False


def test_way_берёт_координаты_из_center() -> None:
    payload = load_json("osm/overpass_kielce.json")
    ziarno = OverpassSource.to_record(payload["elements"][1])
    assert ziarno is not None
    assert ziarno.lat == pytest.approx(50.8702)


def test_запрос_строится_по_категориям() -> None:
    source = OverpassSource(settings=load_settings())
    query = source.build_query((50.8, 20.6, 50.9, 20.7), ["restauracja", "kawiarnia"])
    assert "amenity=restaurant" in query
    assert "amenity=cafe" in query
    assert "out center tags;" in query


def test_бюджет_отказывает_а_не_тратит() -> None:
    """Потолок по умолчанию — ноль: случайный прогон не выставит счёт."""
    governor = BudgetGovernor(0.0)
    with pytest.raises(BudgetExceeded, match="потолок"):
        governor.charge(0.032, "google places text search")
    assert governor.spent == 0.0


def test_бюджет_проверяется_до_вызова() -> None:
    governor = BudgetGovernor(0.05)
    governor.charge(0.032, "первый вызов")
    with pytest.raises(BudgetExceeded):
        governor.charge(0.032, "второй вызов")
    assert governor.spent == pytest.approx(0.032)
