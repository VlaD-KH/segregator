"""Стадия ``audit``: загрузить страницы бизнеса и прогнать по ним пробы.

Единственное место, где сеть встречается с пробами. Всё, что здесь делается, —
собрать :class:`Context` и передать его чистым функциям. Логика «что означает
найденное» живёт в пробах, а не здесь.
"""

from __future__ import annotations

import asyncio
from urllib.parse import urljoin, urlparse

import httpx

from prospektor.config import Settings
from prospektor.fetch import Fetcher, Page
from prospektor.models import Business, Signal
from prospektor.probes import Context, run_all
from prospektor.store import Store

PSI_URL = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"

# Внутренние страницы, где чаще всего живут меню, контакты и кнопка заказа.
# Ищем по тексту ссылки, а не по адресу: адреса у всех свои, слова — общие.
_SUBPAGE_WORDS: dict[str, tuple[str, ...]] = {
    "menu": ("menu", "jadłospis", "jadlospis", "karta dań", "oferta", "cennik"),
    "contact": ("kontakt", "contact", "napisz"),
    "order": ("zamów", "zamow", "dostawa", "delivery", "rezerwacja"),
}


def _normalize_website(raw: str | None) -> str | None:
    if not raw:
        return None
    url = raw.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    parsed = urlparse(url)
    return url if parsed.netloc else None


def _pick_subpages(home: Page, limit_per_kind: int = 1) -> dict[str, str]:
    """Выбрать по одной внутренней странице каждого вида.

    Ограничение жёсткое: аудит не должен превращаться в обход всего сайта —
    это и медленно, и невежливо по отношению к чужому хостингу.
    """
    from prospektor.probes.htmldoc import parse

    doc = parse(home.body)
    base = home.final_url or home.url
    found: dict[str, str] = {}
    for anchor in doc.anchors:
        href = anchor.attrs.get("href") or ""
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        haystack = f"{href} {anchor.text}".lower()
        for kind, words in _SUBPAGE_WORDS.items():
            if kind in found or not any(word in haystack for word in words):
                continue
            absolute = urljoin(base, href)
            if urlparse(absolute).netloc == urlparse(base).netloc:
                found[kind] = absolute
        if len(found) >= len(_SUBPAGE_WORDS) * limit_per_kind:
            break
    return found


async def _pagespeed(url: str, api_key: str) -> dict | None:
    params = {"url": url, "key": api_key, "category": ["performance", "seo", "accessibility"]}
    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            resp = await client.get(PSI_URL, params=params)
            resp.raise_for_status()
            return resp.json()
    except (httpx.HTTPError, ValueError):
        # PageSpeed капризен и часто отваливается по таймауту. Отсутствие
        # метрики не должно валить весь аудит.
        return None


async def audit_business(
    fetcher: Fetcher,
    store: Store,
    biz: Business,
    *,
    render: bool = False,
    pagespeed_key: str | None = None,
) -> list[Signal]:
    website = _normalize_website(biz.website)
    map_tags = {
        f.field: f.value
        for f in store.facts_for(biz.id)
        if f.field.startswith("osm_") or f.field == "opening_hours"
    }
    ctx = Context(business=biz, map_tags=map_tags)

    if website:
        ctx.home = await fetcher.get(website)
        origin = f"{urlparse(website).scheme}://{urlparse(website).netloc}"
        ctx.robots_txt = await fetcher.robots_text(website)
        ctx.llms_txt = await fetcher.get(urljoin(origin, "/llms.txt"), respect_robots=False)
        if ctx.home.ok:
            for kind, url in _pick_subpages(ctx.home).items():
                ctx.pages[kind] = await fetcher.get(url)
            if render:
                ctx.rendered = await fetcher.rendered(website)
            if pagespeed_key:
                ctx.psi = await _pagespeed(website, pagespeed_key)

    signals = run_all(ctx)
    store.put_signals(biz.id, signals)
    return signals


async def audit_all(
    settings: Settings,
    store: Store,
    businesses: list[Business],
    *,
    render: bool = False,
    on_done: object = None,
) -> int:
    """Проаудировать список. Параллелизм ограничен настройкой ``concurrency``."""
    limit = asyncio.Semaphore(settings.concurrency)
    key = settings.pagespeed_api_key
    done = 0

    async with Fetcher(settings, store) as fetcher:

        async def one(biz: Business) -> None:
            nonlocal done
            async with limit:
                await audit_business(
                    fetcher, store, biz, render=render, pagespeed_key=key
                )
                done += 1
                if callable(on_done):
                    on_done(done, biz)

        await asyncio.gather(*(one(b) for b in businesses))
    return done
