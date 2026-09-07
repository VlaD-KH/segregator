"""Группа B: SEO и согласованность NAP."""

from __future__ import annotations

from prospektor.probes import seo
from tests.conftest import make_context


def signals(ctx) -> dict:
    return {s.key: s.value for s in seo.probe(ctx)}


def test_нормальная_страница_проходит_базовые_проверки() -> None:
    result = signals(make_context("sushi_z_jsonld.html"))
    assert result["seo.title_ok"] is True
    assert result["seo.description_ok"] is True
    assert result["seo.h1_ok"] is True
    assert result["seo.canonical"] is True
    assert result["seo.indexable"] is True
    assert result["seo.sitemap_declared"] is True


def test_длинный_title_и_два_h1_ловятся() -> None:
    result = signals(make_context("pizzeria_menu_obrazek.html"))
    assert result["seo.title_ok"] is False       # 80+ символов, обрежется в выдаче
    assert result["seo.description_ok"] is False  # «Pizza.»
    assert result["seo.h1_ok"] is False           # два h1
    assert result["seo.canonical"] is False


def test_глухой_запрет_в_robots_снимает_индексируемость() -> None:
    result = signals(make_context("sushi_z_jsonld.html", robots="blocks_all.txt"))
    assert result["seo.indexable"] is False


def test_расхождение_телефона_между_сайтом_и_картой() -> None:
    совпадает = signals(make_context("sushi_z_jsonld.html", phone="+48123456789"))
    assert совпадает["seo.nap_phone_match"] is True

    расходится = signals(make_context("sushi_z_jsonld.html", phone="+48999888777"))
    assert расходится["seo.nap_phone_match"] is False


def test_город_с_карты_должен_встречаться_на_сайте() -> None:
    result = signals(make_context("sushi_z_jsonld.html", city="Kielce"))
    assert result["seo.nap_city_match"] is True
    чужой = signals(make_context("sushi_z_jsonld.html", city="Radom"))
    assert чужой["seo.nap_city_match"] is False
