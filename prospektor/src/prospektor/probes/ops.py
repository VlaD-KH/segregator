"""Группа D — автоматизация приёма заказов и общения.

Самый коммерчески понятный разрыв. Три состояния, между которыми и идёт разговор
с владельцем:

* заказов онлайн нет вообще — единственный канал «позвонить в часы работы»;
* заказы есть, но только через агрегатор — оборот идёт мимо, с комиссией;
* есть собственный канал — здесь чинить нечего, это не лид.

Отпечатки платформ живут в :mod:`prospektor.platforms` — тем же таблицам нужна
проба по адресу, которая работает до загрузки страницы.
"""

from __future__ import annotations

import re

from prospektor.models import Signal
from prospektor.platforms import (
    BOOKING_PLATFORMS,
    CHAT_WIDGETS,
    DELIVERY_AGGREGATORS,
    OWN_ORDER_WIDGETS,
    UrlKind,
    classify_url,
    detect,
)
from prospektor.probes.context import Context

_ORDER_WORDS = (
    "zamów online", "zamow online", "zamów teraz", "zamawiaj online",
    "złóż zamówienie", "zloz zamowienie", "order online", "koszyk",
)
# Своя форма записи без внешней платформы: ищем по словам, а не по домену
_RESERVATION_WORDS = (
    "rezerwacja online", "zarezerwuj stolik", "zarezerwuj wizytę", "umów wizytę",
    "book a table", "rezerwuj termin",
)
_FORM = re.compile(r"<form[^>]*>", re.IGNORECASE)
_MAILTO = re.compile(r"mailto:", re.IGNORECASE)


def probe(ctx: Context) -> list[Signal]:
    out: list[Signal] = []
    evidence = ctx.home.evidence() if ctx.home else None
    haystack = ctx.all_html()

    aggregators = detect(haystack, DELIVERY_AGGREGATORS)
    own_widgets = detect(haystack, OWN_ORDER_WIDGETS)
    reservations = detect(haystack, BOOKING_PLATFORMS)
    chats = detect(haystack, CHAT_WIDGETS)

    # Адрес из карточки участвует наравне со страницей: у салона, чей «сайт» —
    # это профиль в Booksy, страницы для загрузки просто нет, но запись работает.
    kind, platform = classify_url(ctx.business.website)
    if kind is UrlKind.BOOKING and platform and platform not in reservations:
        reservations.append(platform)
    if kind is UrlKind.DELIVERY and platform and platform not in aggregators:
        aggregators.append(platform)

    has_cart_words = any(word in haystack for word in _ORDER_WORDS)
    has_reservation_words = any(word in haystack for word in _RESERVATION_WORDS)
    own_order = bool(own_widgets) or has_cart_words
    online_order = own_order or bool(aggregators)

    out.append(Signal.data("ops.aggregators", sorted(aggregators), evidence))
    out.append(Signal.data("ops.order_widgets", own_widgets, evidence))
    out.append(Signal.flag("ops.online_order", online_order, evidence))
    out.append(Signal.flag("ops.own_order_channel", own_order, evidence))
    # Главный питч: спрос обслуживается, но через чужую кассу с комиссией
    out.append(Signal.flag("ops.aggregator_only", bool(aggregators) and not own_order, evidence))

    out.append(Signal.data("ops.reservation_widgets", sorted(reservations), evidence))
    out.append(
        Signal.flag("ops.reservation", bool(reservations) or has_reservation_words, evidence)
    )
    # Запись есть, но целиком на чужой площадке — своего канала нет.
    out.append(
        Signal.flag(
            "ops.booking_platform_only",
            bool(reservations) and not has_reservation_words and not own_order,
            evidence,
        )
    )
    out.append(Signal.data("ops.chat_widgets", chats, evidence))
    out.append(Signal.flag("ops.chat", bool(chats), evidence))

    has_form = bool(_FORM.search(haystack))
    has_mail = bool(_MAILTO.search(haystack)) or bool(ctx.business.email)
    out.append(Signal.flag("ops.contact_form", has_form, evidence))
    out.append(Signal.flag("ops.email_reachable", has_mail, evidence))

    # «Только телефон» — когда есть куда звонить и больше никаких каналов
    phone_only = bool(ctx.business.phone) and not (
        online_order or reservations or has_reservation_words or chats or has_form
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
