"""M2.3 и M2.4: отбор кандидатов и склейка сетей.

Обе проверки построены на том, что вскрыл прогон по Старгарду: 13% выборки —
поштоматы, банкоматы и точки сетей, а два филиала сетей склеились в одну карточку.
"""

from __future__ import annotations

import pytest

from prospektor.candidates import is_chain, is_junk_name, select
from prospektor.models import Business, Fact, RawRecord, Signal
from prospektor.resolve import ingest


@pytest.mark.parametrize("name", ["Żabka", "żabka nr 1234", "Paczkomat InPost",
                                  "Automat DHL BOX 24/7", "Western Union",
                                  "0143 ORLEN - Stargard", "Biedronka 421"])
def test_сетевые_точки_распознаются(name) -> None:
    assert is_chain(name), name


@pytest.mark.parametrize("name", ["Wok & Roll", "Gagaś Barber Shop", "Cukiernia Szarlotka",
                                  "Pierogarnia"])
def test_независимый_бизнес_не_считается_сетью(name) -> None:
    assert not is_chain(name)


def test_адрес_вместо_названия() -> None:
    assert is_junk_name("Stargard Ul. Wojska Polskiego")
    assert is_junk_name("ul. Kardynała Wyszyńskiego")
    assert not is_junk_name("Salon Urody Agnieszka")


def _add(store, bid, name, cats, website=None, signals=None):
    store.upsert_business(
        Business(id=bid, name=name, categories=cats, website=website, city="Szczecin")
    )
    if signals:
        store.put_signals(bid, signals)


def test_отбор_отсеивает_мусор_и_объясняет_причину(store) -> None:
    _add(store, "salon", "Studio Belleze", ["beauty_salon"])
    _add(store, "barber", "Gagaś Barber Shop", ["barber"], website="https://gagas.example.pl")
    _add(store, "locker", "Paczkomat InPost SZC01", ["package_locker"])
    _add(store, "atm", "Bankomat Euronet", ["atms"])
    _add(store, "zabka", "Żabka", ["convenience_store"])
    _add(store, "street", "Szczecin Ul. Wojska Polskiego", ["beauty_salon"])
    _add(store, "resto", "Pierogarnia", ["restaurant"])

    chosen, rejected = select(store, categories=["beauty_pl"])
    assert {b.id for b in chosen} == {"salon", "barber"}

    причины = {r.business_id: r.reason for r in rejected}
    assert причины["locker"] == "нелидовая категория"
    assert причины["atm"] == "нелидовая категория"
    assert причины["zabka"] == "точка сети или франшизы"
    assert причины["street"] == "название не является названием бизнеса"
    assert причины["resto"] == "вне выбранных категорий"


def test_сначала_те_у_кого_есть_сайт(store) -> None:
    """У них аудит даёт больше сигналов, значит и оценка обоснованнее."""
    _add(store, "bez", "Aaa Salon", ["beauty_salon"])
    _add(store, "zsite", "Zzz Salon", ["beauty_salon"], website="https://zzz.example.pl")
    chosen, _ = select(store, categories=["beauty_pl"])
    assert [b.id for b in chosen] == ["zsite", "bez"]


def test_уже_проверенные_пропускаются(store) -> None:
    from prospektor.candidates import AUDIT_MARKER

    _add(store, "new", "Nowy Salon", ["beauty_salon"])
    _add(store, "old", "Stary Salon", ["beauty_salon"],
         signals=[Signal.number(AUDIT_MARKER, 1.0)])
    chosen, rejected = select(store, categories=["beauty_pl"])
    assert [b.id for b in chosen] == ["new"]
    assert any(r.reason == "уже проверен" for r in rejected)


def test_сигналы_с_этапа_enrich_не_считаются_аудитом(store) -> None:
    """Классификация адреса пишет сигналы до аудита — она не должна снимать кандидата."""
    _add(store, "salon", "Studio Belleze", ["beauty_salon"],
         website="http://belleze.booksy.com/",
         signals=[Signal.data("web.url_kind", "booking"),
                  Signal.flag("web.platform_profile_only", True)])
    chosen, _ = select(store, categories=["beauty_pl"])
    assert [b.id for b in chosen] == ["salon"]


def _record(name, ref, lat, lon, street, website=None):
    facts = [Fact(field="street", value=street, source="overture", confidence=0.8)]
    if website:
        facts.append(Fact(field="website", value=website, source="overture", confidence=0.6))
    return RawRecord(source="overture", ref=ref, name=name, facts=facts, lat=lat, lon=lon)


def test_филиалы_сети_не_склеиваются(store) -> None:
    """Ровно случай Western Union из Старгарда: два отделения на одной улице."""
    записи = [
        _record("Western Union", "wu1", 53.336, 15.041, "Wyszyńskiego 8"),
        _record("Western Union", "wu2", 53.3365, 15.0415, "Wyszyńskiego 22d"),
    ]
    created, updated = ingest(store, записи)
    assert (created, updated) == (2, 0)


def test_одна_и_та_же_точка_сети_склеивается(store) -> None:
    """Та же сеть, тот же адрес из другого источника — это одна карточка."""
    записи = [
        _record("Żabka", "z1", 53.336, 15.041, "Wyszyńskiego 8"),
        _record("Żabka", "z2", 53.3361, 15.0411, "Wyszyńskiego 8"),
    ]
    created, updated = ingest(store, записи)
    assert (created, updated) == (1, 1)


def test_независимый_бизнес_склеивается_по_имени_и_близости(store) -> None:
    """Правило для сетей не должно ломать обычную дедупликацию."""
    записи = [
        _record("Gagaś Barber Shop", "osm1", 53.336, 15.041, "Rynek 4"),
        _record("Gagas Barber Shop", "ov1", 53.3361, 15.0411, "Rynek 4a"),
    ]
    created, updated = ingest(store, записи)
    assert (created, updated) == (1, 1)


def test_профиль_видит_и_сырые_значения_источников() -> None:
    """Карточка хранит `beauty_salon` из Overture и `hairdresser` из OSM,
    а профиль оперирует каноническим `fryzjer`. Без отображения отбор молча
    выбрасывал бы всех, у кого имя категории в источнике не совпало случайно."""
    from prospektor.taxonomy import expand_to_source_values

    beauty = expand_to_source_values(["beauty_pl"])
    assert {"beauty_salon", "spas", "beauty_and_spa", "hairdresser", "barber",
            "nail_salon", "tattoo_and_piercing"} <= beauty
    assert "restaurant" not in beauty
