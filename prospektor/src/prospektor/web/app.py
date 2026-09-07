"""Дашборд: read-only витрина поверх той же SQLite.

Ничего не пишет и не ходит в сеть — это принципиально: смотреть на данные
должно быть безопасно. Фронтенда как проекта нет, только HTMX из одного
подключаемого файла, поэтому и сборки нет.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates

from prospektor.config import load_settings
from prospektor.export import render_dossier
from prospektor.models import Business
from prospektor.scoring import Scored
from prospektor.scoring.engine import load_profile, score_business
from prospektor.store import Store

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

# Сигналы, вынесенные в фасеты. Не все — только те, по которым реально режут выборку.
FACETS: list[tuple[str, str]] = [
    ("web.has_site", "Есть сайт"),
    ("ops.online_order", "Приём заказов онлайн"),
    ("ops.aggregator_only", "Только через агрегатор"),
    ("ops.phone_only", "Только телефон"),
    ("aio.localbusiness_schema", "Разметка Schema.org"),
    ("aio.menu_machine_readable", "Меню читается машиной"),
    ("aio.menu_is_image_only", "Меню картинкой"),
    ("aio.ai_crawlers_allowed", "ИИ-роботы допущены"),
    ("aio.js_dependent", "Контент только после JS"),
    ("seo.indexable", "Индексируется"),
]


def create_app(profile_name: str = "horeca_pl") -> FastAPI:
    settings = load_settings()
    store = Store(settings.db_path)
    profile = load_profile(profile_name)
    app = FastAPI(title="prospektor", docs_url=None, redoc_url=None)

    def rows(params: dict[str, Any]) -> list[dict[str, Any]]:
        where = ["1=1"]
        args: list[Any] = [profile.name]
        city = params.get("city")
        if city:
            where.append("b.city = ?")
            args.append(city)
        search = params.get("q")
        if search:
            where.append("b.name LIKE ?")
            args.append(f"%{search}%")
        for key, _ in FACETS:
            value = params.get(key)
            if value in ("1", "0"):
                args.append(key)
                args.append(int(value))
                where.append(
                    "EXISTS (SELECT 1 FROM signals s WHERE s.business_id = b.id "
                    "AND s.key = ? AND s.value_bool = ?)"
                )
        sql = f"""
            SELECT b.*, sc.gap, sc.fit, sc.priority, sc.breakdown
            FROM businesses b
            LEFT JOIN scores sc ON sc.business_id = b.id AND sc.profile = ?
            WHERE {' AND '.join(where)}
            ORDER BY sc.priority DESC NULLS LAST
            LIMIT 300
        """
        return [dict(r) for r in store.conn.execute(sql, args)]

    def cities() -> list[str]:
        return [
            r["city"]
            for r in store.conn.execute(
                "SELECT DISTINCT city FROM businesses WHERE city IS NOT NULL ORDER BY city"
            )
        ]

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> Any:
        params = dict(request.query_params)
        data = rows(params)
        for row in data:
            row["top_gap"] = _top_gap(row.get("breakdown"))
        return TEMPLATES.TemplateResponse(
            request,
            "index.html",
            {
                "rows": data,
                "facets": FACETS,
                "cities": cities(),
                "params": params,
                "profile": profile,
            },
        )

    def scored_for(business_id: str, biz: Business) -> Scored:
        """Оценка карточки: сохранённая, если стадия score уже прошла.

        Иначе список и карточка показывали бы разные числа: таблица сортируется
        по сохранённым оценкам, а пересчёт на лету дал бы свои. Пересчитываем
        только то, что ещё ни разу не оценивалось.
        """
        row = store.conn.execute(
            "SELECT gap, fit, priority, reachable, breakdown FROM scores "
            "WHERE business_id = ? AND profile = ?",
            (business_id, profile.name),
        ).fetchone()
        if row is None:
            return score_business(profile, biz, store.signals_for(business_id))
        return Scored(
            gap=row["gap"],
            fit=row["fit"],
            priority=row["priority"],
            reachable=bool(row["reachable"]),
            breakdown=json.loads(row["breakdown"] or "{}"),
        )

    @app.get("/lead/{business_id}", response_class=HTMLResponse)
    def lead(request: Request, business_id: str) -> Any:
        biz = store.get_business(business_id)
        if biz is None:
            return HTMLResponse("<p>Не найдено</p>", status_code=404)
        signals = store.signals_for(business_id)
        scored = scored_for(business_id, biz)
        return TEMPLATES.TemplateResponse(
            request,
            "lead.html",
            {
                "biz": biz,
                "scored": scored,
                "signals": dict(sorted(signals.items())),
                "evidence": store.signal_evidence(business_id),
            },
        )

    @app.get("/lead/{business_id}/dossier", response_class=PlainTextResponse)
    def dossier(business_id: str) -> Any:
        biz = store.get_business(business_id)
        if biz is None:
            return PlainTextResponse("Не найдено", status_code=404)
        signals = store.signals_for(business_id)
        scored = scored_for(business_id, biz)
        return render_dossier(biz, scored, signals, store.signal_evidence(business_id), profile)

    return app


def _top_gap(breakdown: str | None) -> str:
    if not breakdown:
        return ""
    try:
        misses = json.loads(breakdown).get("misses", [])
    except ValueError:
        return ""
    return (misses[0].get("why") or misses[0]["signal"]) if misses else ""
