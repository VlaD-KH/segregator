"""Группа A: присутствие в вебе."""

from __future__ import annotations

from prospektor.probes import web
from tests.conftest import make_context


def signals(ctx) -> dict:
    return {s.key: s.value for s in web.probe(ctx)}


def test_живой_сайт_считается_сайтом() -> None:
    result = signals(make_context("sushi_z_jsonld.html"))
    assert result["web.has_site"] is True
    assert result["web.is_placeholder"] is False
    assert result["web.mobile_viewport"] is True
    assert result["web.text_len"] > 200


def test_заглушка_домена_не_считается_сайтом() -> None:
    result = signals(make_context("parked.html"))
    assert result["web.is_placeholder"] is True
    assert result["web.mobile_viewport"] is False


def test_страница_в_соцсети_это_не_сайт() -> None:
    ctx = make_context("kawiarnia_glovo_only.html", website="https://facebook.com/ziarno")
    result = signals(ctx)
    assert result["web.only_social"] is True
    # Ключевое: наличие страницы в Facebook не должно засчитываться как сайт,
    # иначе заведение выпадет из выборки лидов.
    assert result["web.has_site"] is False


def test_сайта_нет_вовсе() -> None:
    result = signals(make_context(None, website=None))
    assert result["web.has_site"] is False
    assert result["web.declared_site"] is False


def test_определяется_движок_и_год_в_подвале() -> None:
    result = signals(make_context("pizzeria_menu_obrazek.html"))
    assert result["web.copyright_year"] == 2019
    result_ok = signals(make_context("sushi_z_jsonld.html"))
    assert result_ok["web.cms"] == "upmenu"
