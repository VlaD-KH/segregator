"""Доступ к SQLite. Тонкий слой поверх stdlib — ORM здесь только мешал бы.

Все записи идемпотентны: повторный прогон обновляет, а не плодит дубликаты.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from prospektor.models import Business, Fact, Run, Signal, utcnow

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False, потому что дашборд обслуживает синхронные
    # обработчики в пуле потоков. Это безопасно: сборка sqlite3 работает в
    # сериализованном режиме (threadsafety == 3), а сам дашборд только читает.
    if sqlite3.threadsafety < 3:  # pragma: no cover — зависит от сборки Python
        raise RuntimeError(
            "sqlite3 собран без сериализованного режима: дашборд запускать нельзя"
        )
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    return conn


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


class Store:
    """Репозиторий. Держит соединение и знает, как класть и доставать сущности."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.conn = connect(path)

    def close(self) -> None:
        self.conn.close()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        with self.conn:
            yield self.conn

    # --- бизнесы ---------------------------------------------------------

    def upsert_business(self, biz: Business) -> None:
        with self.tx() as conn:
            conn.execute(
                """
                INSERT INTO businesses (id, name, country, city, postal_code, street, lat, lon,
                                        phone, email, website, categories, cuisines, rating,
                                        reviews_count, first_seen, last_seen)
                VALUES (:id, :name, :country, :city, :postal_code, :street, :lat, :lon,
                        :phone, :email, :website, :categories, :cuisines, :rating,
                        :reviews_count, :first_seen, :last_seen)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    city = COALESCE(excluded.city, businesses.city),
                    postal_code = COALESCE(excluded.postal_code, businesses.postal_code),
                    street = COALESCE(excluded.street, businesses.street),
                    lat = COALESCE(excluded.lat, businesses.lat),
                    lon = COALESCE(excluded.lon, businesses.lon),
                    phone = COALESCE(excluded.phone, businesses.phone),
                    email = COALESCE(excluded.email, businesses.email),
                    website = COALESCE(excluded.website, businesses.website),
                    categories = excluded.categories,
                    cuisines = excluded.cuisines,
                    rating = COALESCE(excluded.rating, businesses.rating),
                    reviews_count = COALESCE(excluded.reviews_count, businesses.reviews_count),
                    last_seen = excluded.last_seen
                """,
                {
                    "id": biz.id,
                    "name": biz.name,
                    "country": biz.country,
                    "city": biz.city,
                    "postal_code": biz.postal_code,
                    "street": biz.street,
                    "lat": biz.lat,
                    "lon": biz.lon,
                    "phone": biz.phone,
                    "email": biz.email,
                    "website": biz.website,
                    "categories": json.dumps(biz.categories, ensure_ascii=False),
                    "cuisines": json.dumps(biz.cuisines, ensure_ascii=False),
                    "rating": biz.rating,
                    "reviews_count": biz.reviews_count,
                    "first_seen": _iso(biz.first_seen),
                    "last_seen": _iso(biz.last_seen),
                },
            )
            for kind, value in biz.refs.items():
                conn.execute(
                    "INSERT OR REPLACE INTO refs (business_id, kind, value) VALUES (?, ?, ?)",
                    (biz.id, kind, value),
                )
            conn.execute(
                "INSERT OR IGNORE INTO retention (business_id, collected_at, purge_after) "
                "VALUES (?, ?, ?)",
                (
                    biz.id,
                    _iso(biz.first_seen),
                    _iso(biz.first_seen + timedelta(days=365)),
                ),
            )

    def get_business(self, business_id: str) -> Business | None:
        row = self.conn.execute(
            "SELECT * FROM businesses WHERE id = ?", (business_id,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_business(row)

    def iter_businesses(self, limit: int | None = None) -> Iterator[Business]:
        sql = "SELECT * FROM businesses ORDER BY name"
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        for row in self.conn.execute(sql):
            yield self._row_to_business(row)

    def _row_to_business(self, row: sqlite3.Row) -> Business:
        refs = {
            r["kind"]: r["value"]
            for r in self.conn.execute(
                "SELECT kind, value FROM refs WHERE business_id = ?", (row["id"],)
            )
        }
        return Business(
            id=row["id"],
            name=row["name"],
            country=row["country"],
            city=row["city"],
            postal_code=row["postal_code"],
            street=row["street"],
            lat=row["lat"],
            lon=row["lon"],
            phone=row["phone"],
            email=row["email"],
            website=row["website"],
            categories=json.loads(row["categories"]),
            cuisines=json.loads(row["cuisines"]),
            refs=refs,
            rating=row["rating"],
            reviews_count=row["reviews_count"],
            first_seen=datetime.fromisoformat(row["first_seen"]),
            last_seen=datetime.fromisoformat(row["last_seen"]),
        )

    def find_by_ref(self, kind: str, value: str) -> str | None:
        row = self.conn.execute(
            "SELECT business_id FROM refs WHERE kind = ? AND value = ?", (kind, value)
        ).fetchone()
        return row["business_id"] if row else None

    # --- факты и сигналы -------------------------------------------------

    def add_facts(self, business_id: str, facts: Iterable[Fact]) -> None:
        with self.tx() as conn:
            conn.executemany(
                """
                INSERT OR IGNORE INTO facts
                    (business_id, field, value, source, source_url, confidence,
                     personal_data, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        business_id,
                        f.field,
                        f.value,
                        f.source,
                        f.source_url,
                        f.confidence,
                        int(f.personal_data),
                        _iso(f.fetched_at),
                    )
                    for f in facts
                ],
            )

    def facts_for(self, business_id: str) -> list[Fact]:
        rows = self.conn.execute(
            "SELECT * FROM facts WHERE business_id = ? ORDER BY field, confidence DESC",
            (business_id,),
        )
        return [
            Fact(
                field=r["field"],
                value=r["value"],
                source=r["source"],
                source_url=r["source_url"],
                confidence=r["confidence"],
                personal_data=bool(r["personal_data"]),
                fetched_at=datetime.fromisoformat(r["fetched_at"]),
            )
            for r in rows
        ]

    def put_signals(self, business_id: str, signals: Iterable[Signal]) -> None:
        with self.tx() as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO signals
                    (business_id, key, kind, value_bool, value_num, value_json,
                     evidence_url, snippet_sha, checked_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        business_id,
                        s.key,
                        str(s.kind),
                        None if s.value_bool is None else int(s.value_bool),
                        s.value_num,
                        None
                        if s.value_json is None
                        else json.dumps(s.value_json, ensure_ascii=False),
                        s.evidence.url if s.evidence else None,
                        s.evidence.snippet_sha if s.evidence else None,
                        _iso(s.checked_at),
                    )
                    for s in signals
                ],
            )

    def signals_for(self, business_id: str) -> dict[str, Any]:
        rows = self.conn.execute(
            "SELECT key, kind, value_bool, value_num, value_json FROM signals "
            "WHERE business_id = ?",
            (business_id,),
        )
        out: dict[str, Any] = {}
        for r in rows:
            if r["kind"] == "bool":
                out[r["key"]] = None if r["value_bool"] is None else bool(r["value_bool"])
            elif r["kind"] == "num":
                out[r["key"]] = r["value_num"]
            else:
                out[r["key"]] = json.loads(r["value_json"]) if r["value_json"] else None
        return out

    def signal_evidence(self, business_id: str) -> dict[str, str]:
        rows = self.conn.execute(
            "SELECT key, evidence_url FROM signals "
            "WHERE business_id = ? AND evidence_url IS NOT NULL",
            (business_id,),
        )
        return {r["key"]: r["evidence_url"] for r in rows}

    def signal_keys(self) -> list[str]:
        rows = self.conn.execute("SELECT DISTINCT key FROM signals ORDER BY key")
        return [r["key"] for r in rows]

    def rebuild_signal_view(self) -> None:
        """Пересобрать плоское вью ``v_business_signals``.

        Сигналы хранятся ключ-значением, а дашбоду и ad-hoc SQL удобнее колонки.
        Вью пересобирается по фактически встреченным ключам — поэтому новый сигнал
        не требует миграции схемы.
        """
        keys = self.signal_keys()
        cols = []
        for key in keys:
            col = key.replace(".", "_")
            cols.append(
                f"MAX(CASE WHEN s.key = '{key}' THEN "
                f"COALESCE(s.value_bool, s.value_num, s.value_json) END) AS \"{col}\""
            )
        select_cols = (",\n       " + ",\n       ".join(cols)) if cols else ""
        with self.tx() as conn:
            conn.execute("DROP VIEW IF EXISTS v_business_signals")
            conn.execute(
                f"""
                CREATE VIEW v_business_signals AS
                SELECT b.*{select_cols}
                FROM businesses b
                LEFT JOIN signals s ON s.business_id = b.id
                GROUP BY b.id
                """
            )

    # --- прогоны и очистка -----------------------------------------------

    def start_run(self, run: Run) -> None:
        with self.tx() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO runs (id, stage, area, profile, legal_mode, "
                "sources_used, cost_spent, started_at, finished_at, notes) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run.id,
                    run.stage,
                    run.area,
                    run.profile,
                    str(run.legal_mode),
                    json.dumps(run.sources_used),
                    run.cost_spent,
                    _iso(run.started_at),
                    None if run.finished_at is None else _iso(run.finished_at),
                    run.notes,
                ),
            )

    def finish_run(self, run_id: str, cost: float, notes: str | None = None) -> None:
        with self.tx() as conn:
            conn.execute(
                "UPDATE runs SET finished_at = ?, cost_spent = ?, notes = ? WHERE id = ?",
                (_iso(utcnow()), cost, notes, run_id),
            )

    def last_run(self, stage: str | None = None) -> str | None:
        sql = "SELECT id FROM runs"
        args: tuple[Any, ...] = ()
        if stage:
            sql += " WHERE stage = ?"
            args = (stage,)
        sql += " ORDER BY started_at DESC LIMIT 1"
        row = self.conn.execute(sql, args).fetchone()
        return row["id"] if row else None

    def purge_older_than(self, days: int) -> int:
        """Удалить бизнесы, собранные раньше порога. Возвращает число удалённых."""
        cutoff = _iso(utcnow() - timedelta(days=days))
        with self.tx() as conn:
            cur = conn.execute(
                "DELETE FROM businesses WHERE id IN "
                "(SELECT business_id FROM retention WHERE collected_at < ?)",
                (cutoff,),
            )
            return cur.rowcount
