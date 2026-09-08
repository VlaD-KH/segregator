"""Overture Maps Places — массовый бесплатный костяк выборки.

~72 млн POI мира лежат parquet-ом на S3: с bbox-фильтром скачивается только
нужный кусок, ключей и регистрации не нужно. Лицензии CDLA Permissive 2.0 /
Apache 2.0 разрешают коммерческое использование — в отличие от Google Maps,
где массовый обход упирается в запрет ToS.

Два способа чтения, и выбор между ними автоматический:

* **duckdb** — если установлен и может загрузить расширение `httpfs`;
* **http** — чтение Range-запросами через pyarrow.

Второй появился вынужденно: в среде разработки бакет доступен, а
`extensions.duckdb.org` закрыт. На практике он оказался и быстрее, и легче —
около 30 МБ на город против установки duckdb с двумя расширениями, — поэтому
остался как полноценный запасной режим, а не как заплатка.

Данные Overture пересобираются раз в месяц и беднее OSM по контактам, поэтому
роль у адаптера ровно одна: дать широкий охват, который потом уточняется
OSM-ом, сайтом и реестром.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx

from prospektor.models import Fact, LegalMode, RawRecord
from prospektor.sources.base import Area, SourceAdapter
from prospektor.sources.httprange import ScanStats, scan_parquet_bbox

S3_TEMPLATE = "s3://overturemaps-us-west-2/release/{release}/theme=places/type=*/*"
S3_HTTP = "https://overturemaps-us-west-2.s3.amazonaws.com/"
PLACES_PREFIX = "release/{release}/theme=places/type=place/"

# Колонки, которые нужны :meth:`OvertureSource.to_record`. Читать остальные 45
# бессмысленно: они увеличили бы объём скачиваемого без пользы.
HTTP_COLUMNS = [
    "id", "names", "categories", "confidence", "websites", "phones", "emails",
    "socials", "addresses", "bbox",
]


class OvertureSource(SourceAdapter):
    name = "overture"
    legal_mode = LegalMode.CLEAN
    cost_per_call = 0.0
    # Стоимость последней выборки в режиме http: сколько файлов и мегабайт ушло.
    last_scan: ScanStats | None = None

    def available(self) -> bool:
        return self.backend() is not None

    def backend(self) -> str | None:
        """Каким способом читать. ``None`` — читать нечем.

        duckdb предпочтительнее, когда он действительно работает: фильтрация
        идёт на его стороне. Но установленный duckdb ещё не значит рабочий —
        расширение `httpfs` докачивается из сети при первом обращении.
        """
        try:
            import duckdb

            connection = duckdb.connect()
            connection.execute("INSTALL httpfs; LOAD httpfs;")
            connection.close()
            return "duckdb"
        except ImportError:
            pass
        except Exception:  # noqa: BLE001 — расширение может не скачаться
            pass
        try:
            import pyarrow  # noqa: F401
        except ImportError:
            return None
        return "http"

    @staticmethod
    def _overture_categories(categories: list[str]) -> set[str]:
        from prospektor.taxonomy import categories as category_map

        out: set[str] = set()
        for category in categories:
            out.update(category_map().get(category, {}).get("overture", []))
        return out

    def build_sql(
        self, bbox: tuple[float, float, float, float], categories: list[str], release: str
    ) -> str:
        south, west, north, east = bbox
        overture_cats = sorted(self._overture_categories(categories))
        # Фильтр по категории — на стороне DuckDB, чтобы не тянуть лишние строки по сети.
        # Проверяются оба поля: зонтичные значения вроде `beauty_and_spa` встречаются
        # только в `alternate`, и по одному `primary` терялась половина заведений.
        cat_filter = ""
        if overture_cats:
            values = ", ".join(f"'{c}'" for c in sorted(set(overture_cats)))
            cat_filter = (
                f"AND (categories.primary IN ({values})"
                f" OR len(list_intersect(categories.alternate, [{values}])) > 0)"
            )
        source = S3_TEMPLATE.format(release=release)
        return f"""
            SELECT id, names.primary AS name, categories.primary AS category,
                   categories.alternate AS category_alt,
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
        bbox = area.require_bbox()
        backend = self.backend()
        if backend == "duckdb":
            rows: Iterator[dict[str, Any]] = self._rows_duckdb(bbox, categories)
        elif backend == "http":
            rows = self._rows_http(bbox, categories)
        else:  # pragma: no cover — адаптер не активируется без бэкенда
            return
        for row in rows:
            record = self.to_record(row)
            if record is not None:
                yield record

    def _rows_duckdb(
        self, bbox: tuple[float, float, float, float], categories: list[str]
    ) -> Iterator[dict[str, Any]]:
        import duckdb

        conn = duckdb.connect()
        conn.execute("INSTALL spatial; LOAD spatial; INSTALL httpfs; LOAD httpfs;")
        conn.execute("SET s3_region='us-west-2';")
        result = conn.execute(self.build_sql(bbox, categories, self.settings.overture_release))
        columns = [d[0] for d in result.description]
        for row in result.fetchall():
            yield dict(zip(columns, row, strict=True))

    def list_files(self, client: httpx.Client, release: str) -> list[str]:
        """Список parquet-файлов темы places в релизе."""
        prefix = PLACES_PREFIX.format(release=release)
        resp = client.get(
            S3_HTTP,
            params={"list-type": "2", "prefix": prefix, "max-keys": "1000"},
            timeout=90.0,
        )
        resp.raise_for_status()
        keys = re.findall(r"<Key>([^<]+)</Key>", resp.text)
        return [S3_HTTP + key for key in keys if key.endswith(".parquet")]

    def _rows_http(
        self, bbox: tuple[float, float, float, float], categories: list[str]
    ) -> Iterator[dict[str, Any]]:
        """Чтение Range-запросами. Фильтр по категории — уже на нашей стороне."""
        wanted = self._overture_categories(categories)
        self.last_scan = ScanStats()
        with httpx.Client(follow_redirects=True) as client:
            files = self.list_files(client, self.settings.overture_release)
            for row in scan_parquet_bbox(
                files, bbox, HTTP_COLUMNS, client, stats=self.last_scan
            ):
                if wanted and not (self._row_categories(row) & wanted):
                    continue
                yield self._flatten(row)

    @staticmethod
    def _row_categories(row: dict[str, Any]) -> set[str]:
        cats = row.get("categories") or {}
        values = {cats.get("primary")}
        values.update(cats.get("alternate") or [])
        return {str(v) for v in values if v}

    @staticmethod
    def _flatten(row: dict[str, Any]) -> dict[str, Any]:
        """Привести строку parquet к тому же виду, что отдаёт duckdb."""
        address = (row.get("addresses") or [{}])[0] or {}
        cats = row.get("categories") or {}
        bbox = row.get("bbox") or {}
        return {
            "id": row.get("id"),
            "name": (row.get("names") or {}).get("primary"),
            "category": cats.get("primary"),
            "category_alt": list(cats.get("alternate") or []),
            "confidence": row.get("confidence"),
            "websites": row.get("websites"),
            "phones": row.get("phones"),
            "emails": row.get("emails"),
            "socials": row.get("socials"),
            "street": address.get("freeform"),
            "city": address.get("locality"),
            "postcode": address.get("postcode"),
            "country": address.get("country"),
            "lat": bbox.get("ymin"),
            "lon": bbox.get("xmin"),
        }

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
        # Дополнительные категории идут отдельными фактами: они точнее основной.
        # У салона с `primary = spas` в alternate лежат `nail_salon` и `beauty_salon`.
        for alt in row.get("category_alt") or []:
            if alt:
                facts.append(
                    Fact(
                        field="category_raw",
                        value=str(alt),
                        source="overture",
                        source_url=url,
                        confidence=0.55,
                    )
                )

        return RawRecord(
            source="overture",
            source_url=url,
            ref=ref,
            name=name,
            facts=facts,
            lat=row.get("lat"),
            lon=row.get("lon"),
            raw={
                "category": row.get("category"),
                "category_alt": list(row.get("category_alt") or []),
                "confidence": row.get("confidence"),
            },
        )
