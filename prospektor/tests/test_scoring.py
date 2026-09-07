"""Скоринг: два числа, которые нельзя смешивать."""

from __future__ import annotations

from prospektor.models import Business
from prospektor.scoring import load_profile, score_business


def биз(**kwargs) -> Business:
    base = {"id": "x", "name": "Testowa", "rating": 4.5, "reviews_count": 150,
            "phone": "+48123456789"}
    base.update(kwargs)
    return Business(**base)


ЦЕЛЫЙ = {
    "web.has_site": True, "web.only_social": False, "web.is_placeholder": False,
    "web.mobile_viewport": True,
    "aio.localbusiness_schema": True, "aio.menu_machine_readable": True,
    "aio.menu_is_image_only": False, "aio.ai_crawlers_allowed": True,
    "aio.diet_tags": True, "aio.js_dependent": False, "aio.jsonld_broken": False,
    "ops.own_order_channel": True, "ops.aggregator_only": False, "ops.phone_only": False,
    "ops.reservation": True, "ops.chat": True,
    "seo.title_ok": True, "seo.description_ok": True, "seo.h1_ok": True,
    "seo.indexable": True, "seo.nap_phone_match": True,
    "rep.contactable": True,
}


def test_целое_заведение_не_имеет_разрыва() -> None:
    profile = load_profile("horeca_pl")
    scored = score_business(profile, биз(), ЦЕЛЫЙ)
    assert scored.gap == 0.0
    assert scored.breakdown["misses"] == []


def test_каждый_промах_попадает_в_разбор_с_весом() -> None:
    profile = load_profile("horeca_pl")
    сломан = {**ЦЕЛЫЙ, "aio.menu_machine_readable": False, "ops.own_order_channel": False}
    scored = score_business(profile, биз(), сломан)
    assert scored.gap > 0
    ключи = {m["signal"] for m in scored.breakdown["misses"]}
    assert ключи == {"aio.menu_machine_readable", "ops.own_order_channel"}
    # Разбор отсортирован по весу: самое дорогое — сверху
    веса = [m["weight"] for m in scored.breakdown["misses"]]
    assert веса == sorted(веса, reverse=True)


def test_непроверенные_сигналы_не_штрафуются() -> None:
    """Иначе непроверенный сайт выглядел бы так же плохо, как проверенный и битый."""
    profile = load_profile("horeca_pl")
    пусто = score_business(profile, биз(), {})
    assert пусто.gap == 0.0


def test_разрыв_и_потенциал_считаются_независимо() -> None:
    """Два заведения с одинаковыми поломками, но разным спросом.

    Это и есть причина, по которой скор не может быть один: разрыв у них
    одинаковый, а ценность как лида — принципиально разная.
    """
    profile = load_profile("horeca_pl")
    сломан = {**ЦЕЛЫЙ, "web.has_site": False, "ops.own_order_channel": False}

    золото = score_business(profile, биз(rating=4.6, reviews_count=400), сломан)
    мусор = score_business(profile, биз(rating=2.2, reviews_count=4), сломан)

    assert золото.gap == мусор.gap
    assert золото.fit > мусор.fit
    assert золото.priority > мусор.priority


def test_нулевой_потенциал_обнуляет_приоритет() -> None:
    profile = load_profile("horeca_pl")
    сломан = {**ЦЕЛЫЙ, "web.has_site": False, "rep.contactable": False}
    никакой = score_business(
        profile, биз(rating=None, reviews_count=0, phone=None, email=None), сломан
    )
    assert никакой.gap > 0
    assert никакой.priority < золотой_приоритет(profile, сломан)


def золотой_приоритет(profile, сигналы) -> float:
    return score_business(profile, биз(rating=4.8, reviews_count=500), сигналы).priority


def test_профиль_читается_с_весами_и_объяснениями() -> None:
    profile = load_profile("horeca_pl")
    assert profile.name == "horeca_pl"
    assert profile.total_weight > 0
    # У самых дорогих правил обязано быть объяснение «почему это стоит денег» —
    # без него досье превращается в список технических придирок.
    дорогие = [r for r in profile.rules if r.weight >= 8]
    assert дорогие and all(r.why for r in дорогие)
