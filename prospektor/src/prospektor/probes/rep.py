"""Группа E — репутация.

Скромная по объёму группа: без платного источника отзывов доступны рейтинг и
их количество. Но именно они отвечают на вопрос, который важнее всех сигналов
о поломках, — есть ли у заведения спрос. Заведение с 4.6 и 400 отзывами без
сайта стоит разговора; с 2.1 и пятью отзывами — нет.
"""

from __future__ import annotations

from prospektor.models import Signal
from prospektor.probes.context import Context

# Порог, ниже которого выборка отзывов не значит ничего
MIN_MEANINGFUL_REVIEWS = 10


def probe(ctx: Context) -> list[Signal]:
    out: list[Signal] = []
    biz = ctx.business
    evidence = None

    if biz.rating is not None:
        out.append(Signal.number("rep.rating", float(biz.rating), evidence))
    if biz.reviews_count is not None:
        count = float(biz.reviews_count)
        out.append(Signal.number("rep.reviews_count", count, evidence))
        out.append(
            Signal.flag("rep.has_traction", count >= MIN_MEANINGFUL_REVIEWS, evidence)
        )
    out.append(
        Signal.flag(
            "rep.contactable",
            bool(biz.phone or biz.email or biz.website),
            evidence,
        )
    )
    return out
