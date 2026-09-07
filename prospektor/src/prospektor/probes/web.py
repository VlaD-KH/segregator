"""Группа A — присутствие в вебе.

Отвечает на вопрос «есть ли вообще что аудировать»: сайт живой, или это парковка
домена, страница в Facebook вместо сайта, или заброшенная вёрстка 2017 года.
"""

from __future__ import annotations

import re

from prospektor.models import Signal
from prospektor.platforms import UrlKind, classify_url
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
    kind, _platform = classify_url(website)
    only_social = kind is UrlKind.SOCIAL
    # Адрес, ведущий на чужую площадку, — не собственный сайт, и это известно
    # без загрузки: у профиля в Booksy или на Facebook нет ни своей разметки,
    # ни своего канала заказа, и владелец им не управляет.
    foreign_profile = declared and kind is not UrlKind.OWN
    reachable = ctx.has_page and not foreign_profile

    out.append(Signal.flag("web.declared_site", declared, evidence))
    out.append(Signal.flag("web.only_social", only_social, evidence))

    if declared:
        out.append(Signal.flag("web.https", website.lower().startswith("https://"), evidence))

    # Ключевое различение. «Сайта нет» — это утверждение о мире, и выставлять его
    # можно только тогда, когда мы это установили: адрес не заявлен вовсе, либо
    # домен не резолвится, либо главная отвечает 404. Таймаут, 403 и обрыв
    # соединения означают «не проверено», и тогда сигнал не выставляется вообще —
    # правило скоринга его пропустит, вместо того чтобы наказать бизнес за наш сбой.
    if reachable:
        out.append(Signal.flag("web.has_site", True, evidence))
        out.append(Signal.flag("web.site_reachable", True, evidence))
    elif foreign_profile:
        out.append(Signal.flag("web.has_site", False, evidence))
    elif not declared:
        out.append(Signal.flag("web.has_site", False, evidence))
        out.append(Signal.flag("web.site_reachable", False, evidence))
    elif ctx.home is not None and ctx.home.absent:
        out.append(Signal.flag("web.has_site", False, evidence))
        out.append(Signal.flag("web.site_reachable", False, evidence))
        out.append(Signal.data("web.check_failed", str(ctx.home.failure), evidence))
    else:
        # Сайт заявлен, но проверить его не удалось: has_site не выставляем.
        reason = str(ctx.home.failure) if ctx.home and ctx.home.failure else "not_fetched"
        out.append(Signal.flag("web.check_inconclusive", True, evidence))
        out.append(Signal.data("web.check_failed", reason, evidence))

    if not ctx.has_page:
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
