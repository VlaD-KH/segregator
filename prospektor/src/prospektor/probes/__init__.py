"""Пробы: чистые функции, превращающие загруженные страницы в сигналы.

Порядок групп значения не имеет — сигналы независимы. Единственная зависимость:
``seo`` и ``aio`` используют разобранный документ из контекста, который парсится
один раз и кэшируется.
"""

from __future__ import annotations

from collections.abc import Callable

from prospektor.models import Signal
from prospektor.probes import aio, ops, rep, seo, web
from prospektor.probes.context import Context

PROBES: dict[str, Callable[[Context], list[Signal]]] = {
    "web": web.probe,
    "seo": seo.probe,
    "aio": aio.probe,
    "ops": ops.probe,
    "rep": rep.probe,
}


def run_all(ctx: Context) -> list[Signal]:
    out: list[Signal] = []
    for probe in PROBES.values():
        out.extend(probe(ctx))
    return out


__all__ = ["PROBES", "Context", "run_all"]
