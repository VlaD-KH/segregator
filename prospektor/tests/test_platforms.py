"""Классификация веб-адреса — основа M2.2.

Все примеры взяты из настоящих данных Старгарда и Щецина: это те самые 8%
значений поля `websites`, которые ведут не на сайт бизнеса.
"""

from __future__ import annotations

import pytest

from prospektor.platforms import UrlKind, classify_url, host_of


@pytest.mark.parametrize(
    ("url", "kind", "platform"),
    [
        ("https://wokandroll.example.pl/", UrlKind.OWN, None),
        ("http://awhairstudio.booksy.com/a/", UrlKind.BOOKING, "booksy"),
        ("https://booksy.com/pl-pl/266177_gagas-barber-shop_barber", UrlKind.BOOKING, "booksy"),
        ("https://www.moment.pl/kreatywna-kosmetologia", UrlKind.BOOKING, "moment"),
        ("https://www.pyszne.pl/menu/la-cucina-italiana", UrlKind.DELIVERY, "pyszne"),
        ("https://glovoapp.com/pl/kielce/ziarno", UrlKind.DELIVERY, "glovo"),
        ("https://www.facebook.com/perfumeria.stargard", UrlKind.SOCIAL, "facebook"),
        ("https://www.instagram.com/pazurroksy_stylizacja", UrlKind.SOCIAL, "instagram"),
        ("https://g.co/kgs/zbSQ9iL", UrlKind.MAPS_LINK, "google"),
        ("https://maps.app.goo.gl/vp4nWPQFQh9XTLFy8", UrlKind.MAPS_LINK, "google"),
        ("http://www.pkt.pl/hydro-centrum/1959936/5-1/", UrlKind.DIRECTORY, "pkt"),
        ("https://allegro.pl/uzytkownik/alleloombard860/sklep", UrlKind.MARKETPLACE, "allegro"),
        ("", UrlKind.NONE, None),
        (None, UrlKind.NONE, None),
    ],
)
def test_классификация_адреса(url, kind, platform) -> None:
    assert classify_url(url) == (kind, platform)


def test_виджет_мессенджера_не_считается_соцсетью_страницей() -> None:
    """Ссылка на плагин чата живёт на facebook.com, но это не «вместо сайта»."""
    kind, platform = classify_url("https://www.facebook.com/plugins/customerchat/")
    assert kind is UrlKind.SOCIAL and platform == "facebook"


def test_host_of_снимает_схему_и_www() -> None:
    assert host_of("https://www.Example.PL/kontakt?a=1") == "example.pl"
    assert host_of("wokandroll.example.pl") == "wokandroll.example.pl"
    assert host_of(None) is None
