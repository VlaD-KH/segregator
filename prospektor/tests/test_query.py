"""Компилятор фильтров: «разовый тематический» срез — это файл, а не код."""

from __future__ import annotations

import pytest

from prospektor.models import Business, Signal
from prospektor.query import QueryError, compile_query, load_query, run_query


def подготовить(store) -> None:
    """Три заведения: целое, «только агрегатор» и без сайта."""
    данные = [
        ("ok", "Wok & Roll", "Kielce", 4.6, 400, {"web.has_site": True,
         "ops.aggregator_only": False, "ops.online_order": True}, ["restauracja"], ["asian"]),
        ("agg", "Ziarno", "Kielce", 4.4, 210, {"web.has_site": True,
         "ops.aggregator_only": True, "ops.online_order": True}, ["kawiarnia"], ["dessert"]),
        ("none", "Da Grasso", "Radom", 4.1, 35, {"web.has_site": False,
         "ops.aggregator_only": False, "ops.online_order": False}, ["pizzeria"], ["italian"]),
    ]
    for bid, name, city, rating, reviews, signals, cats, cuisines in данные:
        store.upsert_business(
            Business(id=bid, name=name, city=city, rating=rating, reviews_count=reviews,
                     categories=cats, cuisines=cuisines)
        )
        store.put_signals(
            bid,
            [Signal.flag(k, v) for k, v in signals.items()]
            + [Signal.number("rep.reviews_count", float(reviews)),
               Signal.number("rep.rating", rating)],
        )
        with store.tx() as conn:
            conn.execute(
                "INSERT INTO scores (business_id, profile, gap, fit, priority, reachable,"
                " breakdown, scored_at) VALUES (?,?,?,?,?,?,?,?)",
                (bid, "horeca_pl", 50.0, 60.0, 55.0, 1, "{}", "2026-09-07T00:00:00+00:00"),
            )


def test_фильтр_по_городу_и_категории(store) -> None:
    подготовить(store)
    rows = run_query(store, {"where": [{"city": "Kielce"}, {"category_in": ["kawiarnia"]}]})
    assert [r["name"] for r in rows] == ["Ziarno"]


def test_фильтр_по_кухне(store) -> None:
    подготовить(store)
    rows = run_query(store, {"where": [{"cuisine_in": ["asian", "italian"]}]})
    assert {r["name"] for r in rows} == {"Wok & Roll", "Da Grasso"}


def test_числовые_операторы_по_сигналу(store) -> None:
    подготовить(store)
    rows = run_query(store, {"where": [{"rep.reviews_count": {"gte": 200}}]})
    assert {r["name"] for r in rows} == {"Wok & Roll", "Ziarno"}


def test_any_of_собирает_разные_дыры(store) -> None:
    подготовить(store)
    rows = run_query(
        store,
        {"where": [{"any_of": [{"web.has_site": False}, {"ops.aggregator_only": True}]}]},
    )
    assert {r["name"] for r in rows} == {"Ziarno", "Da Grasso"}


def test_готовый_файл_фильтра_компилируется(store) -> None:
    подготовить(store)
    spec = load_query("horeca-bez-cyfrowych-zamowien")
    rows = run_query(store, spec)
    # Da Grasso: 35 отзывов и нет сайта — попадает. Wok & Roll целый — не попадает.
    assert "Da Grasso" in {r["name"] for r in rows}
    assert "Wok & Roll" not in {r["name"] for r in rows}


def test_неизвестное_поле_объясняется_человеку() -> None:
    with pytest.raises(QueryError, match="неизвестное поле"):
        compile_query({"where": [{"вымышленное": 1}]})


def test_сортировка_только_по_разрешённым_полям() -> None:
    with pytest.raises(QueryError, match="нельзя сортировать"):
        compile_query({"order_by": "breakdown desc"})


def test_параметры_подставляются_а_не_склеиваются() -> None:
    """Значения не должны попадать в текст SQL — иначе это инъекция."""
    sql, params = compile_query({"where": [{"city": "Kielce'; DROP TABLE businesses; --"}]})
    assert "DROP TABLE" not in sql
    assert "Kielce'; DROP TABLE businesses; --" in params
