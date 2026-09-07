"""Группа C: читаемость для ИИ-агентов — главная группа модуля."""

from __future__ import annotations

from prospektor.probes import aio
from tests.conftest import load_robots, make_context


def signals(ctx) -> dict:
    return {s.key: s.value for s in aio.probe(ctx)}


def test_полная_разметка_ресторана_распознаётся() -> None:
    result = signals(make_context("sushi_z_jsonld.html"))
    assert result["aio.jsonld_present"] is True
    assert result["aio.jsonld_broken"] is False
    assert result["aio.localbusiness_schema"] is True
    assert result["aio.menu_machine_readable"] is True
    assert result["aio.menu_items_count"] == 2
    assert result["aio.opening_hours_schema"] is True
    assert result["aio.geo_schema"] is True
    assert "restaurant" in result["aio.jsonld_types"]


def test_теги_диет_и_синонимы_блюд_видны() -> None:
    """Ровно тот случай из исходных материалов: «Том Ям без кинзы».

    Заведение выигрывает конкуренцию не красивым сайтом, а тем, что состав и
    диетические маркеры лежат в разметке.
    """
    result = signals(make_context("sushi_z_jsonld.html"))
    assert result["aio.diet_tags"] is True
    assert result["aio.alt_names"] is True
    assert result["aio.price_in_markup"] is True


def test_меню_картинкой_ловится() -> None:
    result = signals(make_context("pizzeria_menu_obrazek.html"))
    assert result["aio.jsonld_present"] is False
    assert result["aio.localbusiness_schema"] is False
    assert result["aio.menu_machine_readable"] is False
    assert result["aio.menu_is_image_only"] is True


def test_меню_картинкой_не_вменяется_при_наличии_разметки() -> None:
    """Осторожность важнее полноты: обвинять заведение зря нельзя."""
    result = signals(make_context("sushi_z_jsonld.html"))
    assert result["aio.menu_is_image_only"] is False


def test_закрытые_ии_роботы() -> None:
    закрыто = signals(make_context("sushi_z_jsonld.html", robots="blocks_ai.txt"))
    assert закрыто["aio.ai_crawlers_allowed"] is False
    assert set(закрыто["aio.ai_crawlers_blocked"]) == {"GPTBot", "ClaudeBot"}

    открыто = signals(make_context("sushi_z_jsonld.html", robots="open.txt"))
    assert открыто["aio.ai_crawlers_allowed"] is True


def test_правило_для_конкретного_бота_важнее_общего() -> None:
    """Так же, как это трактуют сами роботы."""
    bots = aio.parse_ai_robots(load_robots("blocks_ai.txt"))
    assert bots["GPTBot"] is False
    assert bots["PerplexityBot"] is True  # для него правила нет, действует общее Allow

    всем_запрещено = aio.parse_ai_robots(load_robots("blocks_all.txt"))
    assert всем_запрещено["PerplexityBot"] is False


def test_контент_только_после_js() -> None:
    ctx = make_context("spa_js_only.html", rendered="spa_js_only.rendered.html")
    result = signals(ctx)
    assert result["aio.js_dependent"] is True
    assert result["aio.raw_text_ratio"] < 0.33


def test_сайта_нет_значит_разметки_нет() -> None:
    """Нули должны быть явными, иначе скоринг не отличит «не проверяли» от «пусто»."""
    result = signals(make_context(None, website=None))
    assert result["aio.jsonld_present"] is False
    assert result["aio.localbusiness_schema"] is False
    assert result["aio.menu_machine_readable"] is False
