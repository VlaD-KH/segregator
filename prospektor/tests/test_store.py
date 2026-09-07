"""Хранилище: провенанс и идемпотентность."""

from __future__ import annotations

from prospektor.models import Business, Fact, Signal
from prospektor.resolve import materialize


def test_повторная_запись_не_плодит_дубликаты(store) -> None:
    biz = Business(id="a", name="Wok", city="Kielce")
    store.upsert_business(biz)
    store.upsert_business(biz)
    assert store.conn.execute("SELECT COUNT(*) c FROM businesses").fetchone()["c"] == 1


def test_конфликт_источников_сохраняется_целиком(store) -> None:
    """Оба телефона остаются в базе — видно, кто что сказал."""
    store.upsert_business(Business(id="a", name="Wok"))
    store.add_facts("a", [
        Fact(field="phone", value="+48111111111", source="overture", confidence=0.6),
        Fact(field="phone", value="+48123456789", source="osm", confidence=0.7),
    ])
    факты = [f for f in store.facts_for("a") if f.field == "phone"]
    assert len(факты) == 2

    # А в карточку попадает тот, чей источник надёжнее
    biz = materialize(store, "a")
    assert biz is not None
    assert biz.phone == "+48123456789"


def test_сигналы_перезаписываются_а_не_накапливаются(store) -> None:
    store.upsert_business(Business(id="a", name="Wok"))
    store.put_signals("a", [Signal.flag("web.has_site", False)])
    store.put_signals("a", [Signal.flag("web.has_site", True)])
    assert store.signals_for("a") == {"web.has_site": True}


def test_вью_сигналов_пересобирается_под_фактические_ключи(store) -> None:
    store.upsert_business(Business(id="a", name="Wok"))
    store.put_signals("a", [Signal.flag("aio.menu_is_image_only", True)])
    store.rebuild_signal_view()
    row = store.conn.execute("SELECT * FROM v_business_signals").fetchone()
    assert row["aio_menu_is_image_only"] == 1


def test_purge_удаляет_вместе_с_контактами(store) -> None:
    from datetime import UTC, datetime, timedelta

    store.upsert_business(Business(id="a", name="Wok", phone="+48123456789"))
    старая_дата = (datetime.now(UTC) - timedelta(days=500)).isoformat()
    with store.tx() as conn:
        conn.execute("UPDATE retention SET collected_at = ?", (старая_дата,))
    assert store.purge_older_than(365) == 1
    assert store.conn.execute("SELECT COUNT(*) c FROM facts").fetchone()["c"] == 0
