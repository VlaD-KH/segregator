"""Группа C — читаемость бизнеса для ИИ-агентов.

Это ядро ценности модуля, и оно прямо выведено из механики, описанной в
исходных материалах: агент принимает решение не «глядя на сайт», а вызывая
инструменты и читая структурированные данные. Отсюда следствия, которые здесь
и измеряются:

* нет JSON-LD — агенту нечего процитировать, он берёт конкурента;
* меню картинкой или PDF — состав блюд не читается, запрос «Том Ям без кинзы»
  проходит мимо;
* нет тегов диет и аллергенов — фильтр «без глютена» выкидывает заведение;
* закрыт GPTBot/ClaudeBot/PerplexityBot — заведения для ИИ просто не существует;
* контент появляется только после JS — робот видит пустую страницу.

Каждый пункт — измеримый и починяемый, поэтому из него получается разговор
с владельцем, а не абстрактное «у вас плохой сайт».
"""

from __future__ import annotations

import re
from typing import Any

from prospektor.models import Signal
from prospektor.probes.context import Context

# Типы Schema.org, которые вообще имеет смысл искать у локального бизнеса
_LOCAL_TYPES = {
    "localbusiness", "restaurant", "cafe", "bakery", "bar", "fastfoodrestaurant",
    "hotel", "lodgingbusiness", "organization", "store", "healthandbeautybusiness",
    "dentist", "automotivebusiness", "professionalservice", "foodestablishment",
}
_MENU_TYPES = {"menu", "menuitem", "menusection", "hasmenu"}

# Роботы ИИ-поисковиков. Если закрыты — заведение невидимо там, где сейчас
# растёт доля запросов.
_AI_BOTS = (
    "GPTBot",
    "OAI-SearchBot",
    "ChatGPT-User",
    "ClaudeBot",
    "anthropic-ai",
    "PerplexityBot",
    "Google-Extended",
    "CCBot",
    "Applebot-Extended",
)

_MENU_WORDS = ("menu", "jadłospis", "jadlospis", "karta dań", "karta dan", "oferta", "cennik")
_IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp", ".pdf")
_PRICE = re.compile(r"\b\d{1,4}[,.]\d{2}\s*(?:zł|zl|pln)\b", re.IGNORECASE)


def _walk(node: Any):
    """Обойти дерево JSON-LD, включая @graph и вложенные объекты."""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


def _types(node: dict) -> set[str]:
    raw = node.get("@type")
    if isinstance(raw, str):
        return {raw.lower()}
    if isinstance(raw, list):
        return {str(t).lower() for t in raw}
    return set()


def parse_ai_robots(robots_txt: str) -> dict[str, bool]:
    """Разрешён ли каждый ИИ-робот в robots.txt.

    Явное правило для конкретного бота приоритетнее общего ``User-agent: *`` —
    так же, как это трактуют сами роботы.
    """
    rules: dict[str, list[str]] = {}
    current: list[str] = []
    for line in robots_txt.splitlines():
        stripped = line.split("#")[0].strip()
        if not stripped:
            continue
        key, _, value = stripped.partition(":")
        key, value = key.strip().lower(), value.strip()
        if key == "user-agent":
            current = rules.setdefault(value.lower(), [])
        elif key == "disallow" and current is not None:
            current.append(value)

    result: dict[str, bool] = {}
    for bot in _AI_BOTS:
        own = rules.get(bot.lower())
        applicable = own if own is not None else rules.get("*", [])
        result[bot] = not any(path.strip() == "/" for path in applicable)
    return result


def probe(ctx: Context) -> list[Signal]:
    out: list[Signal] = []
    evidence = ctx.home.evidence() if ctx.home else None

    # robots.txt проверяется даже без работающего сайта: домен может отвечать,
    # а главная — нет.
    bots = parse_ai_robots(ctx.robots_txt)
    out.append(Signal.data("aio.ai_crawlers", bots, evidence))
    blocked = [bot for bot, allowed in bots.items() if not allowed]
    out.append(Signal.flag("aio.ai_crawlers_allowed", not blocked, evidence))
    if blocked:
        out.append(Signal.data("aio.ai_crawlers_blocked", blocked, evidence))

    llms_ok = ctx.llms_txt is not None and ctx.llms_txt.ok and bool(ctx.llms_txt.body.strip())
    out.append(Signal.flag("aio.llms_txt", llms_ok, evidence))

    if not ctx.has_page:
        # Нет сайта — нет и разметки. Пишем это явными нулями, иначе скоринг
        # не отличит «не проверяли» от «проверили, там пусто».
        out.append(Signal.flag("aio.jsonld_present", False, evidence))
        out.append(Signal.flag("aio.localbusiness_schema", False, evidence))
        out.append(Signal.flag("aio.menu_machine_readable", False, evidence))
        return out

    doc = ctx.doc
    blocks = doc.jsonld_blocks()
    parsed = doc.jsonld()
    out.append(Signal.flag("aio.jsonld_present", bool(blocks), evidence))
    # Блок есть, а JSON не разбирается — разметка сломана. Отдельный,
    # довольно частый случай, и чинится он за пять минут.
    out.append(Signal.flag("aio.jsonld_broken", bool(blocks) and not parsed, evidence))

    nodes = [n for root in parsed for n in _walk(root)]
    all_types = sorted({t for n in nodes for t in _types(n)})
    out.append(Signal.data("aio.jsonld_types", all_types, evidence))
    out.append(
        Signal.flag(
            "aio.localbusiness_schema",
            bool(set(all_types) & _LOCAL_TYPES),
            evidence,
        )
    )
    out.append(
        Signal.flag(
            "aio.opening_hours_schema",
            any("openinghours" in t for t in all_types)
            or any("openingHours" in n for n in nodes),
            evidence,
        )
    )
    out.append(
        Signal.flag("aio.geo_schema", any("geo" in n or "address" in n for n in nodes), evidence)
    )

    # --- меню ---------------------------------------------------------
    menu_nodes = [n for n in nodes if _types(n) & _MENU_TYPES or "hasMenu" in n]
    items = [n for n in nodes if "menuitem" in _types(n)]
    machine_menu = bool(menu_nodes or items)
    out.append(Signal.flag("aio.menu_machine_readable", machine_menu, evidence))
    out.append(Signal.number("aio.menu_items_count", float(len(items)), evidence))

    offers = [n for n in nodes if "offer" in _types(n) and n.get("price") is not None]
    out.append(
        Signal.flag(
            "aio.price_in_markup",
            bool(offers) or bool(_PRICE.search(doc.text)) and machine_menu,
            evidence,
        )
    )

    diets = [n.get("suitableForDiet") for n in nodes if n.get("suitableForDiet")]
    out.append(Signal.flag("aio.diet_tags", bool(diets), evidence))
    out.append(
        Signal.flag(
            "aio.alt_names",
            any(n.get("alternateName") for n in nodes),
            evidence,
        )
    )

    image_menu = _menu_is_image_only(ctx)
    out.append(Signal.flag("aio.menu_is_image_only", image_menu and not machine_menu, evidence))

    # --- зависимость от JS --------------------------------------------
    if ctx.rendered is not None and ctx.rendered.ok:
        from prospektor.probes.htmldoc import visible_text_length

        raw_len = len(doc.text)
        rendered_len = visible_text_length(ctx.rendered.body)
        ratio = raw_len / rendered_len if rendered_len else 1.0
        out.append(Signal.number("aio.raw_text_ratio", round(ratio, 3), evidence))
        # Меньше трети текста в исходном HTML — робот без JS видит пустую страницу
        out.append(Signal.flag("aio.js_dependent", ratio < 0.33, evidence))
    return out


def _menu_is_image_only(ctx: Context) -> bool:
    """Меню выложено картинкой или PDF, а не текстом.

    Ищем среди ссылок и картинок «меню-подобные» адреса с расширением файла.
    Проверка сознательно узкая: лучше пропустить случай, чем обвинить заведение
    в том, чего нет.
    """
    doc = ctx.doc
    candidates: list[str] = []
    for anchor in doc.anchors:
        href = (anchor.attrs.get("href") or "").lower()
        label = (anchor.text or "").lower()
        if href.endswith(_IMAGE_EXT) and (
            any(w in href for w in _MENU_WORDS) or any(w in label for w in _MENU_WORDS)
        ):
            candidates.append(href)
    for image in doc.images:
        src = (image.attrs.get("src") or "").lower()
        alt = (image.attrs.get("alt") or "").lower()
        if any(w in src for w in _MENU_WORDS) or any(w in alt for w in _MENU_WORDS):
            candidates.append(src)
    return bool(candidates)
