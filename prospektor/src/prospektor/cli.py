"""Командный интерфейс.

Стадии разделены намеренно: discover ходит в карты, audit — на сайты, score
не ходит никуда. Каждая пишется в базу под своим run_id, поэтому прерванный
прогон продолжается с места остановки, а не начинается заново.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from prospektor.config import Settings, load_settings
from prospektor.models import LegalMode, Run
from prospektor.store import Store

app = typer.Typer(
    add_completion=False,
    help="Поиск бизнесов и скоринг цифровых разрывов: сайт, приём заказов, SEO, читаемость для ИИ.",
)
console = Console()


def _store() -> tuple[Store, Settings]:
    settings = load_settings()
    return Store(settings.db_path), settings


def _run_id(stage: str) -> str:
    return f"{stage}-{uuid.uuid4().hex[:8]}"


@app.command()
def discover(
    area: Annotated[str, typer.Option(help="Город или регион, например «Kielce»")],
    profile: Annotated[str, typer.Option(help="Профиль — задаёт набор категорий")] = "horeca_pl",
    categories: Annotated[str, typer.Option(help="Свои категории через запятую")] = "",
    sources: Annotated[str, typer.Option(help="Ограничить источники: osm,overture")] = "",
    legal_mode: Annotated[str, typer.Option(help="clean | gray")] = "clean",
) -> None:
    """Найти кандидатов на территории и положить их в базу."""
    from prospektor.resolve import ingest
    from prospektor.sources import Area, BudgetGovernor, build_sources
    from prospektor.taxonomy import category_sets, resolve_categories

    store, settings = _store()
    wanted = (
        resolve_categories([c.strip() for c in categories.split(",") if c.strip()])
        if categories
        else category_sets().get(profile, [])
    )
    if not wanted:
        console.print(f"[red]Не удалось определить категории для профиля «{profile}»[/red]")
        raise typer.Exit(2)

    allow_gray = legal_mode == "gray" and settings.allow_gray
    if legal_mode == "gray" and not allow_gray:
        console.print(
            "[red]Режим gray требует PROSPEKTOR_ALLOW_GRAY=1. "
            "Это осознанное решение: такие источники нарушают ToS площадок.[/red]"
        )
        raise typer.Exit(2)

    governor = BudgetGovernor(settings.budget_usd)
    names = [s.strip() for s in sources.split(",") if s.strip()] or None
    adapters = build_sources(settings, governor, names, allow_gray=allow_gray)
    if not adapters:
        console.print("[red]Нет доступных источников[/red]")
        raise typer.Exit(1)

    run = Run(
        id=_run_id("discover"),
        stage="discover",
        area=area,
        profile=profile,
        legal_mode=LegalMode.GRAY if allow_gray else LegalMode.CLEAN,
        sources_used=[a.name for a in adapters],
    )
    store.start_run(run)
    console.print(
        f"Область: [bold]{area}[/bold] · категорий: {len(wanted)} · "
        f"источники: {', '.join(a.name for a in adapters)}"
    )

    async def collect() -> tuple[int, int]:
        records = []
        for adapter in adapters:
            async for record in adapter.discover(Area(name=area), wanted):
                records.append(record)
            console.print(f"  {adapter.name}: всего записей {len(records)}")
        return ingest(store, records)

    created, updated = asyncio.run(collect())
    store.finish_run(run.id, governor.spent, f"новых {created}, обновлено {updated}")
    console.print(f"[green]Новых {created}, обновлено {updated}[/green] · прогон {run.id}")


@app.command()
def enrich() -> None:
    """Пересобрать карточки из фактов (разрешение конфликтов между источниками)."""
    from prospektor.resolve import materialize

    store, _ = _store()
    count = 0
    for biz in list(store.iter_businesses()):
        materialize(store, biz.id)
        count += 1
    console.print(f"[green]Пересобрано карточек: {count}[/green]")


@app.command()
def audit(
    profile: Annotated[str, typer.Option(help="Ограничить категориями профиля")] = "",
    limit: Annotated[int, typer.Option(help="Сколько карточек проверить")] = 0,
    render: Annotated[bool, typer.Option(help="Рендерить в браузере (проба js_dependent)")] = False,
    only_new: Annotated[bool, typer.Option(help="Пропускать уже проверенные")] = True,
    explain: Annotated[bool, typer.Option(help="Показать, кто и почему отсеян")] = False,
) -> None:
    """Загрузить сайты и прогнать пробы. Единственная стадия, которая ходит на чужие сайты.

    Обходятся не все карточки подряд, а отобранные кандидаты: сети, поштоматы,
    банкоматы и школы отсеиваются до первого запроса.
    """
    import collections

    from prospektor.audit import audit_all
    from prospektor.candidates import select

    store, settings = _store()
    categories = [profile] if profile else None
    targets, rejected = select(
        store, categories=categories, limit=limit or None, skip_audited=only_new
    )

    reasons = collections.Counter(r.reason for r in rejected)
    if reasons:
        console.print("Отсеяно до аудита:")
        for reason, count in reasons.most_common():
            console.print(f"  {count:5}  {reason}")
    if explain:
        for r in rejected[:40]:
            console.print(f"    [dim]{r.name[:44]:46} {r.reason}[/dim]")

    if not targets:
        console.print("Кандидатов на проверку нет.")
        return

    run = Run(id=_run_id("audit"), stage="audit", profile=profile or None)
    store.start_run(run)
    console.print(f"К проверке: [bold]{len(targets)}[/bold], параллельно {settings.concurrency}")

    def progress(done: int, biz: object) -> None:
        if done % 10 == 0 or done == len(targets):
            console.print(f"  проверено {done}/{len(targets)}")

    done = asyncio.run(audit_all(settings, store, targets, render=render, on_done=progress))
    store.rebuild_signal_view()
    store.finish_run(run.id, 0.0, f"проверено {done}")
    console.print(f"[green]Проверено: {done}[/green]")


@app.command()
def score(profile: Annotated[str, typer.Option()] = "horeca_pl") -> None:
    """Посчитать gap/fit/priority по профилю. Сеть не используется."""
    from prospektor.scoring.engine import load_profile, score_all

    store, _ = _store()
    prof = load_profile(profile)
    count = score_all(store, prof)
    console.print(f"[green]Оценено карточек: {count}[/green] по профилю «{prof.label}»")


@app.command()
def query(
    spec: Annotated[str, typer.Argument(help="Имя или путь файла фильтра из queries/")],
    profile: Annotated[str, typer.Option()] = "horeca_pl",
    export: Annotated[str, typer.Option(help="csv | xlsx | none")] = "none",
    no_pii: Annotated[
        bool, typer.Option("--no-pii", help="Выгрузка без контактов")
    ] = False,
    out: Annotated[str, typer.Option(help="Путь файла выгрузки")] = "",
) -> None:
    """Отфильтровать базу и при необходимости выгрузить результат."""
    from prospektor.export import to_csv, to_xlsx
    from prospektor.query import load_query, run_query

    store, settings = _store()
    query_spec = load_query(spec)
    rows = run_query(store, query_spec, profile)
    console.print(f"Найдено: [bold]{len(rows)}[/bold] по фильтру «{query_spec.get('name', spec)}»")

    table = Table(show_lines=False)
    for column in ("Название", "Город", "Разрыв", "Потенциал", "Приоритет", "Сайт"):
        table.add_column(column)
    for row in rows[:25]:
        table.add_row(
            row["name"],
            row.get("city") or "—",
            f"{row.get('gap') or 0:.0f}",
            f"{row.get('fit') or 0:.0f}",
            f"{row.get('priority') or 0:.0f}",
            (row.get("website") or "—")[:44],
        )
    console.print(table)

    if export != "none":
        name = query_spec.get("name", "export")
        path = Path(out) if out else settings.export_dir / f"{name}.{export}"
        writer = to_xlsx if export == "xlsx" else to_csv
        written = writer(rows, path, no_pii=no_pii)
        note = " (без контактов)" if no_pii else ""
        console.print(f"[green]Выгружено{note}: {written}[/green]")


@app.command()
def dossier(
    business_id: Annotated[str, typer.Argument()],
    profile: Annotated[str, typer.Option()] = "horeca_pl",
    out: Annotated[str, typer.Option(help="Куда сохранить markdown")] = "",
) -> None:
    """Досье на один лид: что сломано, чем подтверждается, что предлагать."""
    from prospektor.export import render_dossier
    from prospektor.scoring.engine import load_profile, score_business

    store, settings = _store()
    biz = store.get_business(business_id)
    if biz is None:
        console.print(f"[red]Не найдено: {business_id}[/red]")
        raise typer.Exit(1)
    prof = load_profile(profile)
    signals = store.signals_for(biz.id)
    scored = score_business(prof, biz, signals)
    text = render_dossier(biz, scored, signals, store.signal_evidence(biz.id), prof)
    if out:
        path = Path(out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        console.print(f"[green]Сохранено: {path}[/green]")
    else:
        console.print(text)


@app.command()
def stats() -> None:
    """Что уже собрано: сколько карточек, сигналов, какие источники."""
    store, settings = _store()
    conn = store.conn
    rows = {
        "карточек": conn.execute("SELECT COUNT(*) c FROM businesses").fetchone()["c"],
        "фактов": conn.execute("SELECT COUNT(*) c FROM facts").fetchone()["c"],
        "сигналов": conn.execute("SELECT COUNT(*) c FROM signals").fetchone()["c"],
        "оценено": conn.execute("SELECT COUNT(*) c FROM scores").fetchone()["c"],
    }
    console.print(f"База: {settings.db_path}")
    for key, value in rows.items():
        console.print(f"  {key}: {value}")
    for row in conn.execute(
        "SELECT source, COUNT(*) c FROM facts GROUP BY source ORDER BY c DESC"
    ):
        console.print(f"  источник {row['source']}: {row['c']} фактов")


@app.command()
def purge(
    older_than: Annotated[
        int, typer.Option(help="Удалить собранное раньше, чем N дней назад")
    ] = 365,
) -> None:
    """Удалить устаревшие карточки вместе с контактами (требование RODO)."""
    store, _ = _store()
    removed = store.purge_older_than(older_than)
    console.print(f"[green]Удалено карточек: {removed}[/green]")


@app.command()
def serve(
    host: Annotated[str, typer.Option()] = "127.0.0.1",
    port: Annotated[int, typer.Option()] = 8080,
    profile: Annotated[str, typer.Option()] = "horeca_pl",
) -> None:
    """Локальный дашборд: фасетные фильтры и карточка лида."""
    try:
        import uvicorn
    except ImportError:
        console.print("[red]Нужен веб-стек: pip install -e '.[web]'[/red]")
        raise typer.Exit(2) from None
    from prospektor.web.app import create_app

    console.print(f"Дашборд: http://{host}:{port}")
    uvicorn.run(create_app(profile), host=host, port=port, log_level="warning")


@app.command()
def signals(business_id: Annotated[str, typer.Argument()]) -> None:
    """Показать все сигналы по карточке — отладка и ручная сверка."""
    store, _ = _store()
    data = store.signals_for(business_id)
    if not data:
        console.print("Сигналов нет")
        return
    evidence = store.signal_evidence(business_id)
    table = Table()
    table.add_column("Сигнал")
    table.add_column("Значение")
    table.add_column("Проверено на")
    for key in sorted(data):
        value = data[key]
        table.add_row(
            key,
            json.dumps(value, ensure_ascii=False)
            if isinstance(value, (list, dict))
            else str(value),
            (evidence.get(key) or "—")[:50],
        )
    console.print(table)


if __name__ == "__main__":
    app()
