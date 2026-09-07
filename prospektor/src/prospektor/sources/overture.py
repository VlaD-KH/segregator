"""Overture Maps Places — массовый бесплатный костяк выборки.

~72 млн POI мира лежат parquet-ом на S3 и читаются DuckDB прямо оттуда: с
bbox-фильтром скачивается только нужный кусок, ключей и регистрации не нужно.
Лицензии CDLA Permissive 2.0 / Apache 2.0 разрешают коммерческое использование —
в отличие от Google Maps, где массовый обход упирается в запрет ToS.

Данные Overture свежее пересобираются раз в месяц и беднее OSM по контактам,
поэтому роль у адаптера ровно одна: дать широкий охват, который потом
уточняется OSM-ом, сайтом и реестром.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from prospektor.models import Fact, LegalMode, RawRecord
from prospektor.sources.base import Area, SourceAdapter

S3_TEMPLATE = "s3://overturemaps-us-west-2/release/{release}/theme=places/type=*/*"


class OvertureSource(SourceAdapter):
    name = "overture"
    legal_mode = LegalMode.CLEAN
    cost_per_call = 0.0

    def available(self) -> bool:
        try:
            import duckdb  # noqa: F401
        except ImportError:
            return False
        return True

    def build_sql(
        self, bbox: tuple[float, float, float, float], categories: list[str], release: str
    ) -> str:
        south, west, north, east = bbox
        overture_cats: list[str] = []
        from prospektor.taxonomy import categories as category_map

        for category in categories:
            overture_cats.extend(category_map().get(category, {}).get("overture", []))
        # Фильтр по категории — на стороне DuckDB, чтобы не тянуть лишние строки по сети
        cat_filter = ""
        if overture_cats:
            values = ", ".join(f"'{c}'" for c in sorted(set(overture_cats)))
            cat_filter = f"AND categories.primary IN ({values})"
        source = S3_TEMPLATE.format(release=release)
        return f"""
            SELECT id, names.primary AS name, categories.primary AS category,
                   confidence, websites, phones, emails, socials,
                   addresses[1].freeform AS street, addresses[1].locality AS city,
                   addresses[1].postcode AS postcode, addresses[1].country AS country,
                   ST_X(ST_GeomFromWKB(geometry)) AS lon,
                   ST_Y(ST_GeomFromWKB(geometry)) AS lat
            FROM read_parquet('{source}', filename=true, hive_partitioning=1)
            WHERE bbox.xmin BETWEEN {west} AND {east}
              AND bbox.ymin BETWEEN {south} AND {north}
              {cat_filter}
        """

    async def discover(self, area: Area, categories: list[str]) -> AsyncIterator[RawRecord]:
        import duckdb

        bbox = area.require_bbox()
        release = self.settings.overture_release
        conn = duckdb.connect()
        conn.execute("INSTALL spatial; LOAD spatial; INSTALL httpfs; LOAD httpfs;")
        conn.execute("SET s3_region='us-west-2';")
        rows = conn.execute(self.build_sql(bbox, categories, release)).fetchall()
        columns = [d[0] for d in conn.description]
        for row in rows:
            record = self.to_record(dict(zip(columns, row, strict=True)))
            if record is not None:
                yield record

    @staticmethod
    def _first(value: Any) -> str | None:
        """Overture отдаёт контакты списками; берём первый непустой."""
        if isinstance(value, (list, tuple)):
            return next((str(v) for v in value if v), None)
        return str(value) if value else None

    @classmethod
    def to_record(cls, row: dict[str, Any]) -> RawRecord | None:
        name = (row.get("name") or "").strip()
        if not name:
            return None
        ref = str(row.get("id"))
        url = f"https://explore.overturemaps.org/#{ref}"
        facts: list[Fact] = []

        def add(field: str, value: Any, confidence: float, pii: bool = False) -> None:
            text = cls._first(value)
            if text:
                facts.append(
                    Fact(
                        field=field,
                        value=text,
                        source="overture",
                        source_url=url,
                        confidence=confidence,
                        personal_data=pii,
                    )
                )

        add("website", row.get("websites"), 0.6)
        add("phone", row.get("phones"), 0.6, pii=True)
        add("email", row.get("emails"), 0.6, pii=True)
        add("city", row.get("city"), 0.65)
        add("postal_code", row.get("postcode"), 0.65)
        add("street", row.get("street"), 0.65)
        add("category_raw", row.get("category"), 0.7)

        return RawRecord(
            source="overture",
            source_url=url,
            ref=ref,
            name=name,
            facts=facts,
            lat=row.get("lat"),
            lon=row.get("lon"),
            raw={"category": row.get("category"), "confidence": row.get("confidence")},
        )
