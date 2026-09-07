"""Группа D: автоматизация приёма заказов."""

from __future__ import annotations

from prospektor.probes import ops
from tests.conftest import make_context


def signals(ctx) -> dict:
    return {s.key: s.value for s in ops.probe(ctx)}


def test_свой_канал_заказа_не_лид() -> None:
    result = signals(make_context("sushi_z_jsonld.html"))
    assert result["ops.online_order"] is True
    assert result["ops.own_order_channel"] is True
    assert result["ops.aggregator_only"] is False
    assert "upmenu" in result["ops.order_widgets"]


def test_только_агрегаторы_главный_питч() -> None:
    result = signals(make_context("kawiarnia_glovo_only.html"))
    assert set(result["ops.aggregators"]) == {"glovo", "wolt"}
    assert result["ops.online_order"] is True
    assert result["ops.own_order_channel"] is False
    # Оборот идёт через чужую кассу с комиссией — это и есть разговор с владельцем
    assert result["ops.aggregator_only"] is True
    assert "messenger" in result["ops.chat_widgets"]


def test_только_телефон() -> None:
    result = signals(make_context("pizzeria_menu_obrazek.html", phone="+48360111 22"))
    assert result["ops.online_order"] is False
    assert result["ops.phone_only"] is True


def test_телефона_нет_значит_и_режима_только_телефон_нет() -> None:
    result = signals(make_context("pizzeria_menu_obrazek.html", phone=None))
    assert result["ops.phone_only"] is False


def test_теги_доставки_с_карты_подхватываются() -> None:
    ctx = make_context("pizzeria_menu_obrazek.html", map_tags={"osm_delivery": "yes"})
    result = signals(ctx)
    assert result["ops.map_delivery"] is True
