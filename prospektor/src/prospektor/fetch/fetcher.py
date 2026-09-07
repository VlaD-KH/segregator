"""Загрузка страниц: единственная точка выхода модуля в сеть.

Три обязательства, зашитые в код, а не оставленные на дисциплину:

* **robots.txt соблюдается.** Мы аудируем чужие сайты, чтобы предложить владельцу
  улучшение — заходить туда, куда он просил не заходить, противоречит самой затее.
* **Пауза между запросами к одному хосту.** Вежливость дешевле бана.
* **Кэш с TTL.** Повторный прогон не ходит в сеть, прерванный — продолжается.

Порт ``Fetcher`` намеренно узкий: чтобы Firecrawl/Exa/Tavily можно было
подключить адаптером для тяжёлых страниц, не таща их в ядро.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx

from prospektor.config import Settings
from prospektor.models import Evidence, utcnow
from prospektor.store import Store


@dataclass
class Page:
    """Результат загрузки. ``ok`` означает «есть тело, с которым можно работать»."""

    url: str
    status: int | None = None
    headers: dict[str, str] = field(default_factory=dict)
    body: str = ""
    error: str | None = None
    final_url: str | None = None
    elapsed_ms: float | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.status is not None and 200 <= self.status < 300

    def evidence(self, note: str | None = None) -> Evidence:
        return Evidence(
            url=self.final_url or self.url,
            fetched_at=utcnow(),
            snippet_sha=Evidence.sha(self.body[:4096]) if self.body else None,
            note=note,
        )


class Fetcher:
    def __init__(self, settings: Settings, store: Store | None = None) -> None:
        self.settings = settings
        self.store = store
        self.ttl = timedelta(hours=settings.cache_ttl_hours)
        self.delay = settings.per_host_delay
        self._last_hit: dict[str, float] = {}
        self._robots: dict[str, RobotFileParser | None] = {}
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> Fetcher:
        self._client = httpx.AsyncClient(
            timeout=self.settings.request_timeout,
            follow_redirects=True,
            headers={"User-Agent": self.settings.user_agent},
        )
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # --- кэш -------------------------------------------------------------

    def _cached(self, url: str, mode: str) -> Page | None:
        if self.store is None:
            return None
        row = self.store.conn.execute(
            "SELECT * FROM fetch_cache WHERE url = ? AND mode = ?", (url, mode)
        ).fetchone()
        if row is None:
            return None
        fetched = datetime.fromisoformat(row["fetched_at"])
        if datetime.now(UTC) - fetched > self.ttl:
            return None
        return Page(
            url=url,
            status=row["status"],
            headers=json.loads(row["headers"]),
            body=row["body"] or "",
            error=row["error"],
            final_url=url,
        )

    def _store_page(self, page: Page, mode: str) -> None:
        if self.store is None:
            return
        with self.store.tx() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO fetch_cache (url, mode, status, headers, body, error, "
                "fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    page.url,
                    mode,
                    page.status,
                    json.dumps(page.headers, ensure_ascii=False),
                    page.body,
                    page.error,
                    utcnow().isoformat(),
                ),
            )

    # --- вежливость ------------------------------------------------------

    async def _throttle(self, host: str) -> None:
        last = self._last_hit.get(host)
        if last is not None:
            wait = self.delay - (time.monotonic() - last)
            if wait > 0:
                await asyncio.sleep(wait)
        self._last_hit[host] = time.monotonic()

    async def allowed(self, url: str) -> bool:
        """Разрешает ли robots.txt заход по этому адресу нашему User-Agent."""
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin not in self._robots:
            self._robots[origin] = await self._load_robots(origin)
        parser = self._robots[origin]
        if parser is None:
            # robots.txt недоступен — трактуем как «разрешено», так же как это
            # делают поисковые роботы.
            return True
        agent = self.settings.user_agent
        return parser.can_fetch(agent, url)

    async def _load_robots(self, origin: str) -> RobotFileParser | None:
        page = await self._raw_get(urljoin(origin, "/robots.txt"))
        if not page.ok or not page.body:
            return None
        parser = RobotFileParser()
        parser.parse(page.body.splitlines())
        return parser

    async def robots_text(self, url: str) -> str:
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        page = await self.get(urljoin(origin, "/robots.txt"), respect_robots=False)
        return page.body if page.ok else ""

    # --- загрузка --------------------------------------------------------

    async def get(self, url: str, respect_robots: bool = True) -> Page:
        cached = self._cached(url, "raw")
        if cached is not None:
            return cached
        if respect_robots and not await self.allowed(url):
            page = Page(url=url, error="disallowed-by-robots")
            self._store_page(page, "raw")
            return page
        page = await self._raw_get(url)
        self._store_page(page, "raw")
        return page

    async def _raw_get(self, url: str) -> Page:
        if self._client is None:
            raise RuntimeError("Fetcher используется вне async with")
        await self._throttle(urlparse(url).netloc)
        started = time.monotonic()
        try:
            resp = await self._client.get(url)
        except httpx.HTTPError as exc:
            return Page(url=url, error=f"{type(exc).__name__}: {exc}")
        return Page(
            url=url,
            status=resp.status_code,
            headers={k.lower(): v for k, v in resp.headers.items()},
            body=resp.text,
            final_url=str(resp.url),
            elapsed_ms=(time.monotonic() - started) * 1000,
        )

    async def rendered(self, url: str) -> Page:
        """Отрендерить страницу в headless-браузере.

        Единственное место, где нужен playwright, и нужен он ровно для одной
        пробы — ``aio.js_dependent``: если текст появляется только после
        выполнения JS, ИИ-агент видит пустую страницу.
        """
        cached = self._cached(url, "rendered")
        if cached is not None:
            return cached
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            return Page(url=url, error="playwright-not-installed")
        try:
            async with async_playwright() as pw:
                browser = await pw.chromium.launch()
                context = await browser.new_context(
                    user_agent=self.settings.user_agent
                )
                tab = await context.new_page()
                response = await tab.goto(url, wait_until="networkidle", timeout=30000)
                body = await tab.content()
                page = Page(
                    url=url,
                    status=response.status if response else None,
                    body=body,
                    final_url=tab.url,
                )
                await browser.close()
        except Exception as exc:  # noqa: BLE001 — браузер падает разнообразно
            page = Page(url=url, error=f"render-failed: {exc}")
        self._store_page(page, "rendered")
        return page
