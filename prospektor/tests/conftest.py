"""Общие фикстуры. Ни одна из них не ходит в сеть."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from prospektor.fetch import Page
from prospektor.models import Business
from prospektor.probes import Context

FIXTURES = Path(__file__).parent / "fixtures"


def load_site(name: str) -> str:
    return (FIXTURES / "sites" / name).read_text(encoding="utf-8")


def load_robots(name: str) -> str:
    return (FIXTURES / "robots" / name).read_text(encoding="utf-8")


def load_json(relative: str) -> dict:
    return json.loads((FIXTURES / relative).read_text(encoding="utf-8"))


def page(name: str, url: str = "https://example.pl/") -> Page:
    return Page(url=url, status=200, headers={}, body=load_site(name), final_url=url)


def make_context(
    site: str | None = None,
    *,
    robots: str = "open.txt",
    name: str = "Testowa",
    website: str | None = "https://example.pl/",
    phone: str | None = "+48123456789",
    city: str | None = "Kielce",
    rating: float | None = 4.5,
    reviews: int | None = 120,
    rendered: str | None = None,
    map_tags: dict[str, str] | None = None,
) -> Context:
    biz = Business(
        id="test", name=name, city=city, website=website, phone=phone,
        rating=rating, reviews_count=reviews,
    )
    ctx = Context(
        business=biz,
        home=page(site) if site else None,
        robots_txt=load_robots(robots),
        map_tags=map_tags or {},
    )
    if rendered:
        ctx.rendered = page(rendered)
    return ctx


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES


@pytest.fixture
def store(tmp_path: Path):
    from prospektor.store import Store

    instance = Store(tmp_path / "test.db")
    yield instance
    instance.close()
