"""Минимальный разбор HTML на stdlib.

Пробам нужно немного: title, meta, заголовки, ссылки, картинки, блоки JSON-LD и
видимый текст. Ради этого не стоит тащить lxml или C-парсер — корневой проект
отказался от них по той же причине (машина без дискретной видеокарты, мало диска).

Парсер намеренно снисходителен к битой разметке: сайты, которые мы аудируем,
как раз потому и являются лидами, что свёрстаны плохо.
"""

from __future__ import annotations

import contextlib
import json
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any

# Теги, содержимое которых не является видимым текстом
_INVISIBLE = {"script", "style", "noscript", "template", "head"}


@dataclass
class Tag:
    name: str
    attrs: dict[str, str]
    text: str = ""


@dataclass
class Doc:
    title: str = ""
    metas: dict[str, str] = field(default_factory=dict)
    links: list[Tag] = field(default_factory=list)      # <link rel=...>
    anchors: list[Tag] = field(default_factory=list)    # <a href=...>
    images: list[Tag] = field(default_factory=list)
    headings: dict[str, list[str]] = field(default_factory=dict)
    scripts: list[Tag] = field(default_factory=list)
    iframes: list[Tag] = field(default_factory=list)
    text: str = ""
    html: str = ""

    def meta(self, name: str) -> str | None:
        return self.metas.get(name.lower())

    def link_href(self, rel: str) -> str | None:
        for tag in self.links:
            if rel.lower() in (tag.attrs.get("rel") or "").lower():
                return tag.attrs.get("href")
        return None

    def jsonld(self) -> list[Any]:
        """Все распарсенные блоки ``application/ld+json``.

        Битый JSON молча пропускается: для сигнала «разметка есть, но сломана»
        важен сам факт наличия блока, он виден по :meth:`jsonld_blocks`.
        """
        out: list[Any] = []
        for tag in self.jsonld_blocks():
            try:
                out.append(json.loads(tag.text))
            except (ValueError, TypeError):
                continue
        return out

    def jsonld_blocks(self) -> list[Tag]:
        return [
            t for t in self.scripts
            if "ld+json" in (t.attrs.get("type") or "").lower()
        ]

    def hrefs(self) -> list[str]:
        return [a.attrs["href"] for a in self.anchors if a.attrs.get("href")]

    def lower_html(self) -> str:
        return self.html.lower()


class _Collector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.doc = Doc()
        self._stack: list[str] = []
        self._buffer: list[str] = []
        self._capture: Tag | None = None

    # --- служебное -------------------------------------------------------

    def _attrs(self, attrs: list[tuple[str, str | None]]) -> dict[str, str]:
        return {k.lower(): (v or "") for k, v in attrs}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        data = self._attrs(attrs)
        self._stack.append(tag)

        if tag == "meta":
            key = data.get("name") or data.get("property") or data.get("http-equiv")
            if key:
                self.doc.metas[key.lower()] = data.get("content", "")
        elif tag == "link":
            self.doc.links.append(Tag(tag, data))
        elif tag == "a":
            self.doc.anchors.append(Tag(tag, data))
        elif tag == "img":
            self.doc.images.append(Tag(tag, data))
        elif tag == "iframe":
            self.doc.iframes.append(Tag(tag, data))
        elif tag == "script" or tag in {"title", "h1", "h2", "h3"}:
            self._capture = Tag(tag, data)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if self._stack and self._stack[-1] == tag.lower():
            self._stack.pop()
        self._capture = None

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self._capture is not None and self._capture.name == tag:
            value = self._capture.text.strip()
            if tag == "title":
                self.doc.title = value
            elif tag == "script":
                self.doc.scripts.append(self._capture)
            else:
                self.doc.headings.setdefault(tag, []).append(value)
            self._capture = None
        while self._stack:
            if self._stack.pop() == tag:
                break

    def handle_data(self, data: str) -> None:
        if self._capture is not None:
            self._capture.text += data
        if not any(t in _INVISIBLE for t in self._stack):
            stripped = data.strip()
            if stripped:
                self._buffer.append(stripped)

    def finish(self) -> Doc:
        self.doc.text = re.sub(r"\s+", " ", " ".join(self._buffer)).strip()
        return self.doc


def parse(html: str) -> Doc:
    collector = _Collector()
    # html.parser спотыкается о совсем битую разметку; то, что успело разобраться,
    # для проб полезнее, чем исключение.
    with contextlib.suppress(Exception):
        collector.feed(html)
    doc = collector.finish()
    doc.html = html
    return doc


def visible_text_length(html: str) -> int:
    """Объём видимого текста. Используется пробой ``aio.js_dependent``."""
    return len(parse(html).text)
