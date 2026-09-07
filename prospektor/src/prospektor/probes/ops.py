"""Группа D — автоматизация приёма заказов и общения.

Самый коммерчески понятный разрыв. Три состояния, между которыми и идёт разговор
с владельцем:

* заказов онлайн нет вообще — единственный канал «позвонить в часы работы»;
* заказы есть, но только через агрегатор — оборот идёт мимо, с комиссией;
* есть собственный канал — здесь чинить нечего, это не лид.

Детект строится на отпечатках конкретных платформ польского рынка: пять служб
доставки покрывают основную массу ресторанов, у бьюти и медицины свои системы
записи.
"""

from __future__ import annotations

import re

from prospektor.models import Signal
from prospektor.probes.context import Context

# Агрегаторы доставки. Ключ — наше имя, значения — что искать в HTML.
AGGREGATORS: dict[str, tuple[str, ...]] = {
    "pyszne": ("pyszne.pl", "thuisbezorgd", "justeat"),
    "glovo": ("glovoapp.com", "glovo.com"),
    "wolt": ("wolt.com",),
    "ubereats": ("ubereats.com", "uber.com/pl/pl/eat"),
    "boltfood": ("food.bolt.eu", "bolt.eu/food"),
}

# Собственные движки заказа/меню — если стоят, свой канал уже есть
OWN_ORDER_WIDGETS: dict[str, tuple[str, ...]] = {
    "upmenu": ("upmenu.pl", "upmenu.com"),
    "restaumatic": ("restaumatic",),
    "skubacz": ("skubacz.pl",),
    "gomobi": ("gomobi.pl",),
    "papu": ("papu.io",),
    "furgonetka": ("furgonetka",),
}

# Онлайн-запись — бьюти, медицина, автосервис
RESERVATION_WIDGETS: dict[str, tuple[str, ...]] = {
    "booksy": ("booksy.com", "booksy.net"),
    "moment": ("moment.pl",),
    "versum": ("versum.com",),
    "opentable": ("opentable.",),
    "resmio": ("resmio.com",),
    "widget_own": ("rezerwacja online", "zarezerwuj stolik", "book a table"),
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

_ORDER_WORDS = (
    "zamów online", "zamow online", "zamów teraz", "zamawiaj online",
    "złóż zamówienie", "zloz zamowienie", "order online", "koszyk",
)
_FORM = re.compile(r"<form[^>]*>", re.IGNORECASE)
_MAILTO = re.compile(r"mailto:", re.IGNORECASE)


def _detect(haystack: str, table: dict[str, tuple[str, ...]]) -> list[str]:
    return sorted(name for name, marks in table.items() if any(m in haystack for m in marks))


def probe(ctx: Context) -> list[Signal]:
    out: list[Signal] = []
    evidence = ctx.home.evidence() if ctx.home else None
    haystack = ctx.all_html()

    aggregators = _detect(haystack, AGGREGATORS)
    own_widgets = _detect(haystack, OWN_ORDER_WIDGETS)
    reservations = _detect(haystack, RESERVATION_WIDGETS)
    chats = _detect(haystack, CHAT_WIDGETS)

    has_cart_words = any(word in haystack for word in _ORDER_WORDS)
    own_order = bool(own_widgets) or has_cart_words
    online_order = own_order or bool(aggregators)

    out.append(Signal.data("ops.aggregators", aggregators, evidence))
    out.append(Signal.data("ops.order_widgets", own_widgets, evidence))
    out.append(Signal.flag("ops.online_order", online_order, evidence))
    out.append(Signal.flag("ops.own_order_channel", own_order, evidence))
    # Главный питч: спрос обслуживается, но через чужую кассу с комиссией
    out.append(Signal.flag("ops.aggregator_only", bool(aggregators) and not own_order, evidence))

    out.append(Signal.data("ops.reservation_widgets", reservations, evidence))
    out.append(Signal.flag("ops.reservation", bool(reservations), evidence))
    out.append(Signal.data("ops.chat_widgets", chats, evidence))
    out.append(Signal.flag("ops.chat", bool(chats), evidence))

    has_form = bool(_FORM.search(haystack))
    has_mail = bool(_MAILTO.search(haystack)) or bool(ctx.business.email)
    out.append(Signal.flag("ops.contact_form", has_form, evidence))
    out.append(Signal.flag("ops.email_reachable", has_mail, evidence))

    # «Только телефон» — когда есть куда звонить и больше никаких каналов
    phone_only = bool(ctx.business.phone) and not (
        online_order or reservations or chats or has_form
    )
    out.append(Signal.flag("ops.phone_only", phone_only, evidence))

    # Теги с карты: владелец сам отметил доставку/навынос — значит спрос есть
    for tag in ("delivery", "takeaway", "reservation"):
        value = ctx.map_tags.get(f"osm_{tag}")
        if value:
            out.append(Signal.flag(f"ops.map_{tag}", value.lower() in {"yes", "only"}, evidence))

    hours_known = bool(ctx.map_tags.get("opening_hours"))
    out.append(Signal.flag("ops.opening_hours_known", hours_known, evidence))
    return out
