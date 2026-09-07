"""Группа A — присутствие в вебе.

Отвечает на вопрос «есть ли вообще что аудировать»: сайт живой, или это парковка
домена, страница в Facebook вместо сайта, или заброшенная вёрстка 2017 года.
"""

from __future__ import annotations

import re

from prospektor.models import Signal
from prospektor.probes.context import Context

# Признаки «сайта нет, а есть заглушка». Проверяются вместе с малым объёмом текста,
# поэтому ложные срабатывания на реальных страницах маловероятны.
_PLACEHOLDER_MARKERS = (
    "strona w budowie",
    "witryna w budowie",
    "under construction",
    "coming soon",
    "wkrótce",
    "domena została zarejestrowana",
    "ta domena jest na sprzedaż",
    "this domain is for sale",
    "kup tę domenę",
    "parked domain",
    "default web page",
    "it works!",
    "index of /",
)

_SOCIAL_HOSTS = ("facebook.com", "fb.me", "instagram.com", "linktr.ee", "tiktok.com")

# Отпечатки движков. Дают понять, что чинить: у сайта на конструкторе разметку
# правит владелец, у самописа — нужен разработчик.
_CMS_FINGERPRINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("wordpress", ("wp-content", "wp-includes", "wp-json")),
    ("wix", ("static.wixstatic.com", "wix.com", "_wixcssineject")),
    ("squarespace", ("squarespace.com", "static1.squarespace")),
    ("shopify", ("cdn.shopify.com", "shopify.js")),
    ("joomla", ("/media/jui/", "joomla")),
    ("drupal", ("/sites/default/files", "drupal.settings")),
    ("webnode", ("webnode",)),
    ("shoper", ("shoper.pl", "/skins/user/")),
    ("idosell", ("idosell", "iai-shop")),
    ("gomobi", ("gomobi.pl",)),
    ("restaumatic", ("restaumatic",)),
    ("upmenu", ("upmenu",)),
)

_YEAR = re.compile(r"(?:©|&copy;|copyright)[^0-9]{0,20}(20\d{2})", re.IGNORECASE)


def probe(ctx: Context) -> list[Signal]:
    out: list[Signal] = []
    website = (ctx.business.website or "").strip()
    evidence = ctx.home.evidence() if ctx.home else None

    declared = bool(website)
    only_social = declared and any(host in website.lower() for host in _SOCIAL_HOSTS)
    reachable = ctx.has_page

    # «Сайт есть» означает работающий собственный сайт. Страница в Facebook —
    # это не сайт: там нет ни разметки, ни меню, ни приёма заказов, и владелец
    # не управляет тем, что о нём прочитает агент.
    out.append(Signal.flag("web.has_site", bool(reachable and not only_social), evidence))
    out.append(Signal.flag("web.declared_site", declared, evidence))
    out.append(Signal.flag("web.only_social", only_social, evidence))
    out.append(Signal.flag("web.site_reachable", reachable, evidence))

    if declared:
        out.append(Signal.flag("web.https", website.lower().startswith("https://"), evidence))

    if not reachable:
        if declared:
            error = ctx.home.error if ctx.home else "not-fetched"
            out.append(Signal.data("web.fetch_error", error, evidence))
        return out

    doc = ctx.doc
    text_low = doc.text.lower()
    placeholder = len(doc.text) < 400 and any(m in text_low for m in _PLACEHOLDER_MARKERS)
    out.append(Signal.flag("web.is_placeholder", placeholder, evidence))
    out.append(Signal.number("web.text_len", float(len(doc.text)), evidence))

    html_low = doc.lower_html()
    cms = next(
        (name for name, marks in _CMS_FINGERPRINTS if any(m in html_low for m in marks)),
        None,
    )
    out.append(Signal.data("web.cms", cms or "unknown", evidence))

    years = [int(y) for y in _YEAR.findall(doc.html)]
    if years:
        out.append(Signal.number("web.copyright_year", float(max(years)), evidence))

    out.append(
        Signal.flag("web.mobile_viewport", doc.meta("viewport") is not None, evidence)
    )
    return out
