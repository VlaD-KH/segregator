"""Сшивание записей из разных источников и материализация карточки.

Задача: OSM, Overture, Google и реестр говорят об одном и том же заведении —
надо понять, что это одно заведение, не склеив при этом две пиццерии одной сети
в соседних кварталах.

Порядок проверок — от самого надёжного признака к самому шаткому:
1. совпадение внешнего идентификатора (уже видели эту запись);
2. совпадение домена сайта — почти безошибочно, у сетей домены общие,
   поэтому требуется ещё и близость по координатам;
3. нормализованное имя + расстояние меньше порога.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Any

from prospektor.candidates import is_chain
from prospektor.models import Business, Fact, RawRecord, utcnow
from prospektor.probes.url import probe_business
from prospektor.sources import SOURCE_TRUST
from prospektor.store import Store
from prospektor.util import business_id, haversine_m, normalize_phone, registrable_domain, slug_name

# Порог склейки. 150 м — эмпирический компромисс: карты расходятся в координатах
# одного заведения на десятки метров, но два разных заведения редко стоят ближе.
MERGE_RADIUS_M = 150.0

# Размер ячейки пространственного индекса в градусах. На широте Польши это около
# 550 м по долготе и 330 м по широте, то есть заведомо больше порога склейки —
# значит достаточно смотреть ячейку кандидата и восемь соседних.
CELL = 0.003


class _Index:
    """Пространственный индекс существующих карточек.

    Без него склейка сравнивала каждую новую запись со всей базой. На городе
    в две тысячи POI это незаметно, на Щецине с его шестнадцатью тысячами —
    четверть миллиарда сравнений и прогон, который не заканчивается. Индекс
    строится один раз на пакет и пополняется по мере вставки.
    """

    __slots__ = ("_cells",)

    def __init__(self, store: Store) -> None:
        self._cells: dict[tuple[int, int], list[dict[str, Any]]] = {}
        rows = store.conn.execute(
            "SELECT id, name, website, street, lat, lon FROM businesses WHERE lat IS NOT NULL"
        ).fetchall()
        for row in rows:
            self.add(
                row["id"], row["name"], row["website"], row["street"], row["lat"], row["lon"]
            )

    @staticmethod
    def _cell(lat: float, lon: float) -> tuple[int, int]:
        return int(lat / CELL), int(lon / CELL)

    def add(
        self,
        business_id: str,
        name: str,
        website: str | None,
        street: str | None,
        lat: float | None,
        lon: float | None,
    ) -> None:
        if lat is None or lon is None:
            return
        entry = {
            "id": business_id,
            "slug": slug_name(name),
            "domain": registrable_domain(website or "") if website else None,
            "street": street,
            "lat": lat,
            "lon": lon,
        }
        self._cells.setdefault(self._cell(lat, lon), []).append(entry)

    def nearby(self, lat: float, lon: float) -> Iterator[dict[str, Any]]:
        """Карточки в ячейке точки и восьми соседних."""
        cy, cx = self._cell(lat, lon)
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                yield from self._cells.get((cy + dy, cx + dx), ())


def _same_address(a: str | None, b: str | None) -> bool:
    """Один и тот же адрес с точностью до написания."""
    if not a or not b:
        return False
    return slug_name(a) == slug_name(b)


def _candidate_id(store: Store, record: RawRecord, index: _Index) -> str | None:
    """Найти карточку, к которой относится запись, или None, если это новый бизнес.

    Порядок проверок — от надёжного признака к шаткому. Отдельно выделены сети:
    у них ни имя, ни домен не различают точки, поэтому «Żabka в 100 метрах» —
    это другая Żabka, а не та же самая. Прогон по Старгарду поймал ровно этот
    случай: два отделения Western Union и два магазина Media Expert схлопнулись
    в одну карточку.
    """
    existing = store.find_by_ref(record.source, record.ref)
    if existing:
        return existing
    if record.lat is None or record.lon is None:
        return None

    facts = {f.field: f.value for f in record.facts}
    domain = registrable_domain(facts.get("website", "")) if facts.get("website") else None
    slug = slug_name(record.name)
    street = facts.get("street")
    chain = is_chain(record.name)

    for entry in index.nearby(record.lat, record.lon):
        if haversine_m(record.lat, record.lon, entry["lat"], entry["lon"]) > MERGE_RADIUS_M:
            continue
        name_match = bool(slug) and entry["slug"] == slug
        if chain:
            # Для сети совпадения имени и близости мало: нужен тот же адрес.
            if name_match and _same_address(street, entry["street"]):
                return entry["id"]
            continue
        if domain and entry["domain"] == domain:
            return entry["id"]
        if name_match:
            return entry["id"]
    return None


def ingest(store: Store, records: Iterable[RawRecord]) -> tuple[int, int]:
    """Положить сырые записи в базу. Возвращает (новых, обновлённых)."""
    created = updated = 0
    index = _Index(store)
    for record in records:
        existing_id = _candidate_id(store, record, index)
        bid = existing_id or business_id(record.name, record.lat, record.lon)
        known = store.get_business(bid)
        biz = known or Business(id=bid, name=record.name, lat=record.lat, lon=record.lon)
        biz.last_seen = utcnow()
        if record.lat is not None and biz.lat is None:
            biz.lat, biz.lon = record.lat, record.lon
        biz.refs[record.source] = record.ref
        store.upsert_business(biz)
        store.add_facts(bid, record.facts)
        if known:
            updated += 1
        else:
            created += 1
            # Новая карточка сразу попадает в индекс: следующие записи пакета
            # должны иметь возможность склеиться с ней.
            street = next((f.value for f in record.facts if f.field == "street"), None)
            website = next((f.value for f in record.facts if f.field == "website"), None)
            index.add(bid, record.name, website, street, record.lat, record.lon)
    return created, updated


def _best(facts: list[Fact], field: str) -> Fact | None:
    """Победивший факт по полю: доверие к источнику × собственная уверенность факта."""
    candidates = [f for f in facts if f.field == field]
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda f: (SOURCE_TRUST.get(f.source, 0.5) * f.confidence, f.fetched_at),
    )


def materialize(store: Store, business_id_: str) -> Business | None:
    """Пересобрать карточку из фактов.

    Идемпотентно: конфликты источников разрешаются одинаково при каждом вызове,
    а сами конфликтующие факты остаются в базе и видны в досье.
    """
    biz = store.get_business(business_id_)
    if biz is None:
        return None
    facts = store.facts_for(business_id_)

    for field in ("city", "postal_code", "street", "website"):
        fact = _best(facts, field)
        if fact:
            setattr(biz, field, fact.value)

    phone_fact = _best(facts, "phone")
    if phone_fact:
        biz.phone = normalize_phone(phone_fact.value) or phone_fact.value
    email_fact = _best(facts, "email")
    if email_fact:
        biz.email = email_fact.value

    biz.cuisines = sorted({f.value for f in facts if f.field == "cuisine"})
    biz.categories = sorted({f.value for f in facts if f.field == "category_raw"})

    rating = _best(facts, "rating")
    if rating:
        biz.rating = float(rating.value)
    reviews = _best(facts, "reviews_count")
    if reviews:
        biz.reviews_count = int(float(reviews.value))

    store.upsert_business(biz)
    # Классификация адреса не требует сети, поэтому считается здесь, а не в аудите:
    # часть сигналов ops становится известна ещё до первого запроса.
    store.put_signals(biz.id, probe_business(biz))
    return biz
