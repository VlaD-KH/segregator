"""M2.1: «не проверено» не должно выдаваться за «сайта нет».

Прогон по Старгарду поймал этот дефект вживую: у заведения был заявлен сайт,
загрузка вернула 403, и модуль записал «сайта нет» — то есть сбой связи
порождал лид на пустом месте.
"""

from __future__ import annotations

import pytest

from prospektor.fetch import Failure
from prospektor.probes import web
from tests.conftest import failed_page, make_context


def signals(ctx) -> dict:
    return {s.key: s.value for s in web.probe(ctx)}


@pytest.mark.parametrize("failure", [Failure.TIMEOUT, Failure.CONNECT, Failure.TLS,
                                     Failure.HTTP_ERROR, Failure.ROBOTS])
def test_неопределённый_сбой_не_выставляет_has_site(failure) -> None:
    """Главное правило: сигнал не выставляется вовсе, и скоринг его пропускает."""
    ctx = make_context(None, website="https://example.pl/", home=failed_page(failure))
    result = signals(ctx)
    assert "web.has_site" not in result
    assert result["web.check_inconclusive"] is True
    assert result["web.check_failed"] == str(failure)


@pytest.mark.parametrize("failure", [Failure.DNS, Failure.NOT_FOUND])
def test_определённое_отсутствие_выставляет_has_site_false(failure) -> None:
    """DNS не резолвится или 404 на главной — это уже утверждение о мире."""
    ctx = make_context(None, website="https://example.pl/", home=failed_page(failure))
    result = signals(ctx)
    assert result["web.has_site"] is False
    assert result["web.check_failed"] == str(failure)
    assert "web.check_inconclusive" not in result


def test_адрес_не_заявлен_значит_сайта_нет() -> None:
    result = signals(make_context(None, website=None))
    assert result["web.has_site"] is False
    assert result["web.declared_site"] is False


def test_профиль_на_чужой_площадке_это_отсутствие_своего_сайта() -> None:
    """И это известно без загрузки — страницу грузить не нужно вообще."""
    ctx = make_context(None, website="http://awhairstudio.booksy.com/a/")
    result = signals(ctx)
    assert result["web.has_site"] is False
    assert "web.check_inconclusive" not in result


def test_рабочий_сайт_остаётся_сайтом() -> None:
    result = signals(make_context("sushi_z_jsonld.html"))
    assert result["web.has_site"] is True
    assert result["web.site_reachable"] is True


def test_скоринг_пропускает_неизмеренное() -> None:
    """Ради чего всё затевалось: непроверенный сайт не должен давать разрыв."""
    from prospektor.models import Business
    from prospektor.scoring import load_profile, score_business

    profile = load_profile("beauty_pl")
    biz = Business(id="x", name="Salon", website="https://example.pl/", rating=4.7,
                   reviews_count=90, phone="+48123456789")

    ctx = make_context(None, website="https://example.pl/", home=failed_page(Failure.TIMEOUT))
    непроверен = score_business(profile, biz, {s.key: s.value for s in web.probe(ctx)})
    сигналы_отказа = {m["signal"] for m in непроверен.breakdown["misses"]}
    assert "web.has_site" not in сигналы_отказа

    ctx_dns = make_context(None, website="https://example.pl/", home=failed_page(Failure.DNS))
    нет_сайта = score_business(profile, biz, {s.key: s.value for s in web.probe(ctx_dns)})
    assert "web.has_site" in {m["signal"] for m in нет_сайта.breakdown["misses"]}
    assert нет_сайта.gap > непроверен.gap
