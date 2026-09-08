"""Отпечатки платформ и классификация веб-адреса.

Одна таблица на весь модуль. Раньше списки агрегаторов жили внутри пробы `ops`,
но прогон по Старгарду показал, что они нужны раньше — на стадии discover, когда
страница ещё не загружена, а поле `websites` из карты уже есть и в 8% случаев
ведёт не на сайт бизнеса, а на его профиль в Booksy, страницу в Facebook или
короткую ссылку Google.

Отсюда две функции: :func:`classify_url` отвечает «что это за адрес», а таблицы
переиспользует проба `ops` для поиска виджетов в HTML.
"""

from __future__ import annotations

import re
from enum import StrEnum

# Доставка еды. Комиссия за заказ — та самая, о которой имеет смысл говорить с рестораном.
DELIVERY_AGGREGATORS: dict[str, tuple[str, ...]] = {
    "pyszne": ("pyszne.pl", "thuisbezorgd", "justeat"),
    "glovo": ("glovoapp.com", "glovo.com"),
    "wolt": ("wolt.com",),
    "ubereats": ("ubereats.com", "uber.com/pl/pl/eat"),
    "boltfood": ("food.bolt.eu", "bolt.eu/food"),
}

# Платформы онлайн-записи. Экономика у них другая — абонентская, а не процент
# с заказа, — поэтому и разговор с владельцем строится иначе.
BOOKING_PLATFORMS: dict[str, tuple[str, ...]] = {
    "booksy": ("booksy.com", "booksy.net"),
    "moment": ("moment.pl",),
    "versum": ("versum.com",),
    "znanylekarz": ("znanylekarz.pl",),
    "opentable": ("opentable.",),
    "resmio": ("resmio.com",),
}

# Свои движки заказа и меню: если стоят — канал уже есть, чинить нечего.
OWN_ORDER_WIDGETS: dict[str, tuple[str, ...]] = {
    "upmenu": ("upmenu.pl", "upmenu.com"),
    "restaumatic": ("restaumatic",),
    "skubacz": ("skubacz.pl",),
    "gomobi": ("gomobi.pl",),
    "papu": ("papu.io",),
}

CHAT_WIDGETS: dict[str, tuple[str, ...]] = {
    "messenger": ("facebook.com/plugins/customerchat", "fb-customerchat"),
    "tawk": ("tawk.to",),
    "smartsupp": ("smartsupp",),
    "crisp": ("crisp.chat",),
    "livechat": ("livechatinc.com", "livechat.com"),
    "whatsapp": ("wa.me/", "api.whatsapp.com"),
    "tidio": ("tidio",),
}

SOCIAL_HOSTS: dict[str, tuple[str, ...]] = {
    "facebook": ("facebook.com", "fb.com", "fb.me"),
    "instagram": ("instagram.com",),
    "tiktok": ("tiktok.com",),
    "linktree": ("linktr.ee", "linktree"),
    "youtube": ("youtube.com", "youtu.be"),
}

# Карточка в справочнике — не сайт: владелец не управляет ни разметкой, ни содержимым.
DIRECTORY_HOSTS: dict[str, tuple[str, ...]] = {
    "pkt": ("pkt.pl",),
    "panoramafirm": ("panoramafirm.pl",),
    "aleo": ("aleo.com",),
    "gowork": ("gowork.pl",),
    "zumi": ("zumi.pl",),
    "targeo": ("targeo.pl",),
}

MARKETPLACE_HOSTS: dict[str, tuple[str, ...]] = {
    "allegro": ("allegro.pl", "allegrolokalnie.pl"),
    "olx": ("olx.pl",),
    "otomoto": ("otomoto.pl",),
    "booking": ("booking.com",),
}

# Короткая ссылка на карточку Google — вообще не веб-присутствие, а ссылка на карту.
MAPS_LINK_HOSTS: tuple[str, ...] = ("g.co/", "goo.gl", "maps.app.goo.gl", "google.com/maps")


class UrlKind(StrEnum):
    OWN = "own"
    BOOKING = "booking"
    DELIVERY = "delivery"
    SOCIAL = "social"
    DIRECTORY = "directory"
    MARKETPLACE = "marketplace"
    MAPS_LINK = "maps_link"
    NONE = "none"


# Порядок важен: платформа проверяется раньше соцсети, потому что ссылка на
# виджет Messenger формально живёт на facebook.com.
_TABLES: tuple[tuple[UrlKind, dict[str, tuple[str, ...]]], ...] = (
    (UrlKind.BOOKING, BOOKING_PLATFORMS),
    (UrlKind.DELIVERY, DELIVERY_AGGREGATORS),
    (UrlKind.DIRECTORY, DIRECTORY_HOSTS),
    (UrlKind.MARKETPLACE, MARKETPLACE_HOSTS),
    (UrlKind.SOCIAL, SOCIAL_HOSTS),
)

_SCHEME = re.compile(r"^\s*(?:https?://)?(?:www\.)?", re.IGNORECASE)


def classify_url(url: str | None) -> tuple[UrlKind, str | None]:
    """Что это за адрес и на какой платформе.

    Возвращает вид и имя платформы (``booksy``, ``facebook``…) либо ``None``,
    если адрес выглядит собственным сайтом.
    """
    if not url or not url.strip():
        return UrlKind.NONE, None
    low = url.strip().lower()
    if any(mark in low for mark in MAPS_LINK_HOSTS):
        return UrlKind.MAPS_LINK, "google"
    for kind, table in _TABLES:
        for platform, marks in table.items():
            if any(mark in low for mark in marks):
                return kind, platform
    return UrlKind.OWN, None


def host_of(url: str | None) -> str | None:
    """Хост без схемы и ``www``. Пригоден для сравнения адресов между источниками."""
    if not url:
        return None
    host = _SCHEME.sub("", url).split("/")[0].split("?")[0].strip().lower()
    return host or None


def detect(haystack: str, table: dict[str, tuple[str, ...]]) -> list[str]:
    """Какие платформы из таблицы упомянуты в тексте страницы."""
    return sorted(name for name, marks in table.items() if any(m in haystack for m in marks))
