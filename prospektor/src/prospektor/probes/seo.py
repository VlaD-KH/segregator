"""Группа B — качество SEO и согласованность NAP.

Здесь нет ничего экзотического: это те же проверки, что делает любой аудит
сайта. Ценность в том, что они считаются массово и складываются в один
сопоставимый разрыв рядом с сигналами aio.* и ops.*.

Отдельно стоит ``seo.nap_*``: расхождение имени, адреса и телефона между
сайтом, картой и реестром — классический локальный дефект, из-за которого
заведение теряет позиции и в поиске, и в ответах ассистентов.
"""

from __future__ import annotations

import re

from prospektor.models import Signal
from prospektor.probes.context import Context
from prospektor.util import normalize_phone

_DIGITS = re.compile(r"\D+")


def _phone_digits(value: str | None) -> str:
    return _DIGITS.sub("", value or "")[-9:]


def probe(ctx: Context) -> list[Signal]:
    out: list[Signal] = []
    if not ctx.has_page:
        return out

    doc = ctx.doc
    evidence = ctx.home.evidence() if ctx.home else None

    title = doc.title.strip()
    out.append(Signal.flag("seo.title_present", bool(title), evidence))
    out.append(Signal.number("seo.title_len", float(len(title)), evidence))
    # 10–65 символов — рабочий диапазон: короче нечего показать в выдаче,
    # длиннее обрезается.
    out.append(Signal.flag("seo.title_ok", 10 <= len(title) <= 65, evidence))

    description = (doc.meta("description") or "").strip()
    out.append(Signal.number("seo.description_len", float(len(description)), evidence))
    out.append(Signal.flag("seo.description_ok", 50 <= len(description) <= 170, evidence))

    h1s = doc.headings.get("h1", [])
    out.append(Signal.number("seo.h1_count", float(len(h1s)), evidence))
    out.append(Signal.flag("seo.h1_ok", len(h1s) == 1, evidence))

    out.append(Signal.flag("seo.canonical", bool(doc.link_href("canonical")), evidence))
    out.append(
        Signal.flag("seo.og_tags", any(k.startswith("og:") for k in doc.metas), evidence)
    )
    out.append(Signal.flag("seo.lang_declared", "lang=" in doc.lower_html()[:2000], evidence))

    robots_meta = (doc.meta("robots") or "").lower()
    noindex = "noindex" in robots_meta or _blocks_all(ctx.robots_txt)
    out.append(Signal.flag("seo.indexable", not noindex, evidence))

    has_sitemap = "sitemap:" in ctx.robots_txt.lower()
    out.append(Signal.flag("seo.sitemap_declared", has_sitemap, evidence))

    # NAP: телефон и город с карты/реестра должны находиться на самом сайте
    site_digits = _DIGITS.sub("", doc.text)
    biz_phone = _phone_digits(normalize_phone(ctx.business.phone or "") or ctx.business.phone)
    if biz_phone:
        out.append(Signal.flag("seo.nap_phone_match", biz_phone in site_digits, evidence))
    if ctx.business.city:
        out.append(
            Signal.flag(
                "seo.nap_city_match",
                ctx.business.city.lower() in doc.text.lower(),
                evidence,
            )
        )

    if ctx.psi:
        for metric in ("performance", "seo", "accessibility", "best-practices"):
            value = _psi_score(ctx.psi, metric)
            if value is not None:
                out.append(Signal.number(f"seo.psi_{metric.replace('-', '_')}", value, evidence))
    return out


def _blocks_all(robots_txt: str) -> bool:
    """Есть ли в robots.txt глухой запрет для всех роботов."""
    agent_all = False
    for line in robots_txt.splitlines():
        stripped = line.split("#")[0].strip().lower()
        if stripped.startswith("user-agent:"):
            agent_all = stripped.split(":", 1)[1].strip() == "*"
        elif (
            agent_all
            and stripped.startswith("disallow:")
            and stripped.split(":", 1)[1].strip() == "/"
        ):
            return True
    return False


def _psi_score(psi: dict, category: str) -> float | None:
    try:
        raw = psi["lighthouseResult"]["categories"][category]["score"]
    except (KeyError, TypeError):
        return None
    return None if raw is None else float(raw) * 100
