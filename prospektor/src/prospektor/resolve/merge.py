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

from collections.abc import Iterable

from prospektor.models import Business, Fact, RawRecord, utcnow
from prospektor.sources import SOURCE_TRUST
from prospektor.store import Store
from prospektor.util import business_id, haversine_m, normalize_phone, registrable_domain, slug_name

# Порог склейки. 150 м — эмпирический компромисс: карты расходятся в координатах
# одного заведения на десятки метров, но два разных заведения одной сети редко
# стоят ближе.
MERGE_RADIUS_M = 150.0


def _candidate_id(store: Store, record: RawRecord) -> str | None:
    existing = store.find_by_ref(record.source, record.ref)
    if existing:
        return existing

    facts = {f.field: f.value for f in record.facts}
    domain = registrable_domain(facts.get("website", "")) if facts.get("website") else None
    slug = slug_name(record.name)

    rows = store.conn.execute(
        "SELECT id, name, website, lat, lon FROM businesses WHERE lat IS NOT NULL"
    ).fetchall()
    for row in rows:
        if record.lat is None or record.lon is None or row["lat"] is None:
            continue
        distance = haversine_m(record.lat, record.lon, row["lat"], row["lon"])
        if distance > MERGE_RADIUS_M:
            continue
        if domain and registrable_domain(row["website"] or "") == domain:
            return row["id"]
        if slug and slug_name(row["name"]) == slug:
            return row["id"]
    return None


def ingest(store: Store, records: Iterable[RawRecord]) -> tuple[int, int]:
    """Положить сырые записи в базу. Возвращает (новых, обновлённых)."""
    created = updated = 0
    for record in records:
        existing_id = _candidate_id(store, record)
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
    return biz
