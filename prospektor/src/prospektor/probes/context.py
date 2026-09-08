"""Вход для проб.

Пробы — чистые функции: на входе уже загруженные страницы и известные факты,
на выходе список сигналов. Ни одна проба не ходит в сеть сама. Отсюда два
следствия: их можно целиком протестировать на замороженных фикстурах, и любой
сигнал воспроизводим — при тех же входных данных получится тот же результат.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from prospektor.fetch import Page
from prospektor.models import Business
from prospektor.probes.htmldoc import Doc, parse


@dataclass
class Context:
    business: Business
    home: Page | None = None
    rendered: Page | None = None
    robots_txt: str = ""
    llms_txt: Page | None = None
    # Дополнительные страницы, найденные по ссылкам: меню, контакты, доставка
    pages: dict[str, Page] = field(default_factory=dict)
    # Результат PageSpeed Insights, если ключ был задан
    psi: dict[str, Any] | None = None
    # Теги, доставшиеся от источника карты: delivery, takeaway, reservation
    map_tags: dict[str, str] = field(default_factory=dict)

    _doc: Doc | None = None

    @property
    def doc(self) -> Doc:
        if self._doc is None:
            self._doc = parse(self.home.body if self.home and self.home.ok else "")
        return self._doc

    @property
    def has_page(self) -> bool:
        return self.home is not None and self.home.ok and bool(self.home.body.strip())

    def all_html(self) -> str:
        """Склейка HTML главной и дополнительных страниц — для поиска виджетов.

        Кнопка «Zamów online» часто живёт не на главной, а на странице контактов
        или меню, поэтому искать только по главной значит недосчитаться сигналов.
        """
        chunks = [self.home.body] if self.has_page and self.home else []
        chunks += [p.body for p in self.pages.values() if p.ok]
        return "\n".join(chunks).lower()

    def evidence_url(self) -> str:
        if self.home is not None:
            return self.home.final_url or self.home.url
        return self.business.website or ""
