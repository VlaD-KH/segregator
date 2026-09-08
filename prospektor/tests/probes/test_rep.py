"""Группа E: репутация как прокси спроса."""

from __future__ import annotations

from prospektor.probes import rep
from tests.conftest import make_context


def signals(ctx) -> dict:
    return {s.key: s.value for s in rep.probe(ctx)}


def test_спрос_подтверждён_отзывами() -> None:
    result = signals(make_context("sushi_z_jsonld.html", rating=4.6, reviews=400))
    assert result["rep.rating"] == 4.6
    assert result["rep.reviews_count"] == 400
    assert result["rep.has_traction"] is True


def test_пять_отзывов_это_не_спрос() -> None:
    result = signals(make_context("sushi_z_jsonld.html", rating=2.1, reviews=5))
    assert result["rep.has_traction"] is False


def test_недоступный_бизнес_не_лид() -> None:
    result = signals(make_context(None, website=None, phone=None))
    assert result["rep.contactable"] is False
