"""Дашборд: только чтение, но из пула потоков.

Тест существует ровно потому, что этот класс ошибок не ловится остальными:
FastAPI выполняет синхронные обработчики в отдельных потоках, а соединение
SQLite создаётся в главном. Без этой проверки дашборд падал бы только вживую.
"""

from __future__ import annotations

import pytest

from prospektor.models import Business, Signal

fastapi = pytest.importorskip("fastapi", reason="дашборд ставится отдельно: .[web]")
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture
def клиент(store, monkeypatch, tmp_path):
    from prospektor.web import app as web_app

    store.upsert_business(
        Business(id="agg", name="Ziarno", city="Kielce", website="https://ziarno.example.pl",
                 rating=4.4, reviews_count=210)
    )
    store.upsert_business(Business(id="ok", name="Wok", city="Kielce", rating=4.6))
    store.put_signals("agg", [Signal.flag("ops.aggregator_only", True),
                              Signal.flag("web.has_site", True)])
    store.put_signals("ok", [Signal.flag("ops.aggregator_only", False),
                             Signal.flag("web.has_site", True)])
    with store.tx() as conn:
        conn.execute(
            "INSERT INTO scores (business_id, profile, gap, fit, priority, reachable,"
            " breakdown, scored_at) VALUES (?,?,?,?,?,?,?,?)",
            ("agg", "horeca_pl", 49.0, 38.0, 44.0, 1,
             '{"misses": [{"signal": "ops.aggregator_only", "expected": false,'
             ' "actual": true, "weight": 12, "why": "Комиссия агрегаторов"}]}',
             "2026-09-07T00:00:00+00:00"),
        )

    monkeypatch.setattr(web_app, "Store", lambda _path: store)
    return TestClient(web_app.create_app("horeca_pl"))


def test_список_отдаётся_из_потока_пула(клиент) -> None:
    ответ = клиент.get("/")
    assert ответ.status_code == 200
    assert "Ziarno" in ответ.text


def test_фасет_режет_выборку(клиент) -> None:
    ответ = клиент.get("/", params={"ops.aggregator_only": "1"})
    assert "Ziarno" in ответ.text
    assert ">Wok<" not in ответ.text


def test_карточка_лида_показывает_evidence(клиент) -> None:
    ответ = клиент.get("/lead/agg")
    assert ответ.status_code == 200
    assert "ops.aggregator_only" in ответ.text
    # Объяснение приходит из сохранённого разбора, а не пересчитывается заново —
    # иначе карточка и список показывали бы разные числа.
    assert "Комиссия агрегаторов" in ответ.text


def test_досье_отдаётся_текстом(клиент) -> None:
    ответ = клиент.get("/lead/agg/dossier")
    assert ответ.status_code == 200
    assert ответ.text.startswith("# Ziarno")



def test_неизвестный_id_даёт_404(клиент) -> None:
    assert клиент.get("/lead/нет-такого").status_code == 404
