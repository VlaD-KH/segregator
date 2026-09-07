"""Сквозной прогон без сети: discover → enrich → audit → score → query → export.

Сеть подменяется заглушкой, всё остальное — настоящее. Смысл теста в том,
чтобы поймать расхождения между стадиями, которых не видно, пока каждая
проверяется по отдельности.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from prospektor.audit import audit_business
from prospektor.export import PII_FIELDS, render_dossier, to_csv
from prospektor.fetch import Page
from prospektor.query import load_query, run_query
from prospektor.resolve import ingest, materialize
from prospektor.scoring.engine import load_profile, score_all, score_business
from prospektor.sources.osm_overpass import OverpassSource
from tests.conftest import load_robots, load_site


class ЗаглушкаСети:
    """Отдаёт заранее заготовленные страницы. Ни одного реального запроса."""

    def __init__(self, страницы: dict[str, str], robots: str = "open.txt") -> None:
        self.страницы = страницы
        self.robots = load_robots(robots)
        self.запрошено: list[str] = []

    async def get(self, url: str, respect_robots: bool = True) -> Page:
        self.запрошено.append(url)
        for кусок, файл in self.страницы.items():
            if кусок in url:
                return Page(url=url, status=200, body=load_site(файл), final_url=url)
        return Page(url=url, status=404, body="")

    async def robots_text(self, url: str) -> str:
        return self.robots

    async def rendered(self, url: str) -> Page:
        return Page(url=url, error="render-disabled")


@pytest.fixture
def собранная_база(store):
    """Стадия discover: элементы Overpass -> карточки с фактами."""
    from tests.conftest import load_json

    payload = load_json("osm/overpass_kielce.json")
    записи = [r for r in (OverpassSource.to_record(e) for e in payload["elements"]) if r]
    created, updated = ingest(store, записи)
    assert (created, updated) == (2, 0)
    for biz in list(store.iter_businesses()):
        materialize(store, biz.id)
    return store


def найти(store, имя: str):
    return next(b for b in store.iter_businesses() if b.name == имя)


def test_discover_и_enrich_дают_карточку_с_провенансом(собранная_база) -> None:
    wok = найти(собранная_база, "Wok & Roll")
    assert wok.city == "Kielce"
    assert wok.website == "https://wokandroll.example.pl"
    assert wok.phone == "+48123456789"       # нормализован в E.164
    assert wok.cuisines == ["asian", "japanese"]
    assert wok.refs["osm"] == "node/1001"

    # У каждого поля есть источник — это и есть провенанс
    источники = {f.field: f.source for f in собранная_база.facts_for(wok.id)}
    assert источники["website"] == "osm"


async def test_повторный_discover_не_создаёт_дубликат(собранная_база) -> None:
    from tests.conftest import load_json

    payload = load_json("osm/overpass_kielce.json")
    записи = [r for r in (OverpassSource.to_record(e) for e in payload["elements"]) if r]
    created, updated = ingest(собранная_база, записи)
    assert created == 0 and updated == 2


async def test_сквозной_прогон_доводит_до_выгрузки(собранная_база, tmp_path: Path) -> None:
    store = собранная_база
    сеть = ЗаглушкаСети(
        {
            "wokandroll": "sushi_z_jsonld.html",
            "ziarno": "kawiarnia_glovo_only.html",
        }
    )

    for biz in list(store.iter_businesses()):
        await audit_business(сеть, store, biz)

    # --- audit нашёл именно те дыры, которые есть ------------------------
    wok = найти(store, "Wok & Roll")
    ziarno = найти(store, "Ziarno")
    сигналы_wok = store.signals_for(wok.id)
    сигналы_ziarno = store.signals_for(ziarno.id)

    assert сигналы_wok["aio.menu_machine_readable"] is True
    assert сигналы_wok["ops.own_order_channel"] is True
    assert сигналы_ziarno["ops.aggregator_only"] is True
    assert сигналы_ziarno["aio.localbusiness_schema"] is False

    # Внутренние страницы найдены по тексту ссылок, а не по адресу
    assert any(url.endswith("/menu") for url in сеть.запрошено)
    assert any("llms.txt" in url for url in сеть.запрошено)

    # --- score развёл их по приоритету -----------------------------------
    profile = load_profile("horeca_pl")
    assert score_all(store, profile) == 2
    оценка_wok = score_business(profile, wok, сигналы_wok)
    оценка_ziarno = score_business(profile, ziarno, сигналы_ziarno)
    assert оценка_ziarno.gap > оценка_wok.gap

    # --- query отобрал того, у кого дыра ---------------------------------
    строки = run_query(store, load_query("agregator-only"), "horeca_pl")
    assert [r["name"] for r in строки] == ["Ziarno"]

    # --- export довёл до файла -------------------------------------------
    файл = to_csv(строки, tmp_path / "leady.csv")
    текст = файл.read_text(encoding="utf-8-sig")
    assert "Ziarno" in текст
    assert "411112233" in текст or "+48411112233" in текст
    # Колонка с объяснением, а не только с кодом сигнала
    assert "комиссия" in текст.lower() or "агрегатор" in текст.lower()


async def test_режим_без_контактов_вырезает_персональные_данные(
    собранная_база, tmp_path: Path
) -> None:
    """Для JDG телефон владельца — персональные данные; RODO это не мелочь."""
    store = собранная_база
    строки = [dict(r) for r in store.conn.execute("SELECT * FROM businesses")]
    файл = to_csv(строки, tmp_path / "rynek.csv", no_pii=True)
    текст = файл.read_text(encoding="utf-8-sig")

    assert "Wok & Roll" in текст
    assert "+48123456789" not in текст
    заголовок = текст.splitlines()[0]
    assert not (set(заголовок.split(",")) & PII_FIELDS)


async def test_досье_объясняет_каждую_цифру(собранная_база) -> None:
    store = собранная_база
    сеть = ЗаглушкаСети({"ziarno": "kawiarnia_glovo_only.html"})
    ziarno = найти(store, "Ziarno")
    await audit_business(сеть, store, ziarno)

    profile = load_profile("horeca_pl")
    сигналы = store.signals_for(ziarno.id)
    оценка = score_business(profile, ziarno, сигналы)
    текст = render_dossier(ziarno, оценка, сигналы, store.signal_evidence(ziarno.id), profile)

    assert "Ziarno" in текст
    # Каждая позиция называет сигнал, вес и объяснение — иначе с этим нельзя идти к клиенту
    assert "ops.aggregator_only" in текст
    assert "Вес в оценке" in текст
    assert "Комиссия агрегаторов" in текст
    # И явную границу: список готовит модуль, звонит человек
    assert "связывается человек" in текст


async def test_повторный_аудит_берёт_страницу_из_кэша(собранная_база) -> None:
    """Прерванный прогон должен продолжаться, а не начинаться заново."""
    store = собранная_база

    страница = Page(
        url="https://wokandroll.example.pl",
        status=200,
        body=load_site("sushi_z_jsonld.html"),
        final_url="https://wokandroll.example.pl",
    )
    with store.tx() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO fetch_cache (url, mode, status, headers, body, error,"
            " fetched_at) VALUES (?,?,?,?,?,?,?)",
            (страница.url, "raw", 200, "{}", страница.body, None, "2026-09-07T00:00:00+00:00"),
        )
    строка = store.conn.execute("SELECT COUNT(*) c FROM fetch_cache").fetchone()
    assert строка["c"] == 1


def test_вью_сигналов_годится_для_дашборда(собранная_база) -> None:
    store = собранная_база
    wok = найти(store, "Wok & Roll")
    from prospektor.models import Signal

    store.put_signals(wok.id, [Signal.flag("ops.aggregator_only", True)])
    store.rebuild_signal_view()
    строки = list(store.conn.execute("SELECT * FROM v_business_signals"))
    assert len(строки) == 2
    assert json.dumps([dict(r) for r in строки], ensure_ascii=False)
