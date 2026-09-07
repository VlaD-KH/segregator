"""Проба по адресу: сигналы без единого сетевого запроса."""

from __future__ import annotations

from prospektor.models import Business
from prospektor.probes.url import probe_business


def signals(website: str | None) -> dict:
    biz = Business(id="x", name="Salon", website=website)
    return {s.key: s.value for s in probe_business(biz)}


def test_профиль_на_площадке_записи_виден_до_аудита() -> None:
    """Тот самый готовый лид-лист: салон живёт только на Booksy."""
    result = signals("http://awhairstudio.booksy.com/a/")
    assert result["web.url_kind"] == "booking"
    assert result["web.url_platform"] == "booksy"
    assert result["web.platform_profile_only"] is True
    assert result["ops.booking_platform"] == "booksy"


def test_агрегатор_доставки_тоже_не_свой_сайт() -> None:
    result = signals("https://www.pyszne.pl/menu/la-cucina-italiana")
    assert result["web.platform_profile_only"] is True
    assert result["ops.delivery_platform"] == "pyszne"


def test_свой_сайт_не_помечается_чужой_площадкой() -> None:
    result = signals("https://wokandroll.example.pl/")
    assert result["web.url_kind"] == "own"
    assert result["web.platform_profile_only"] is False
    assert "ops.booking_platform" not in result


def test_ссылка_на_карточку_google_это_не_присутствие() -> None:
    result = signals("https://g.co/kgs/zbSQ9iL")
    assert result["web.url_kind"] == "maps_link"
    assert result["web.platform_profile_only"] is True


def test_адреса_нет_вовсе() -> None:
    result = signals(None)
    assert result["web.url_kind"] == "none"
    assert result["web.platform_profile_only"] is False


def test_проба_не_трогает_сигналы_чужого_владельца() -> None:
    """ops.reservation принадлежит пробе ops: два источника перезаписали бы друг друга."""
    result = signals("http://awhairstudio.booksy.com/a/")
    assert "ops.reservation" not in result
    assert "ops.online_order" not in result
