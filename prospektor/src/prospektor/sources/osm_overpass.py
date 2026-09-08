"""OpenStreetMap через Overpass API.

Почему это основной бесплатный источник для мелкого локального бизнеса: в OSM
у POI есть готовые теги ``website``, ``phone``, ``opening_hours``, ``cuisine``,
``delivery``, ``takeaway`` — то есть половина нужных нам фактов приходит сразу,
без обхода сайта.

Лицензия ODbL: коммерческое использование разрешено при указании авторства.
Публичный инстанс Overpass — волонтёрский; для регулярных прогонов поднимается
свой (адрес меняется через ``PROSPEKTOR_OVERPASS_URL``), а пока — вежливый
таймаут и одна попытка повтора.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import httpx

from prospektor.models import Fact, LegalMode, RawRecord
from prospektor.sources.base import Area, SourceAdapter
from prospektor.taxonomy import normalize_cuisine, osm_filters

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

# Теги, значение которых и есть категория заведения. Без них карточка,
# найденная только в OSM, оставалась бы без вертикали и выпадала из любого
# профильного скоринга.
_CATEGORY_TAGS = ("amenity", "shop", "leisure", "tourism", "office", "healthcare", "craft")

# Теги OSM -> поля нашей модели. Уверенность ниже, чем у реестра, но выше,
# чем у эвристик с сайта: данные вносит человек, знающий это место.
_TAG_FACTS: dict[str, tuple[str, float, bool]] = {
    # тег: (поле, confidence, персональные данные)
    "phone": ("phone", 0.7, True),
    "contact:phone": ("phone", 0.7, True),
    "email": ("email", 0.7, True),
    "contact:email": ("email", 0.7, True),
    "website": ("website", 0.75, False),
    "contact:website": ("website", 0.75, False),
    "addr:city": ("city", 0.8, False),
    "addr:postcode": ("postal_code", 0.8, False),
    "opening_hours": ("opening_hours", 0.7, False),
    "contact:facebook": ("facebook", 0.6, False),
    "contact:instagram": ("instagram", 0.6, False),
}


class OverpassSource(SourceAdapter):
    name = "osm"
    legal_mode = LegalMode.CLEAN
    cost_per_call = 0.0

    async def geocode(self, name: str) -> tuple[float, float, float, float] | None:
        """Получить bbox по названию города через Nominatim."""
        headers = {"User-Agent": self.settings.user_agent}
        params: dict[str, str | int] = {"q": name, "format": "jsonv2", "limit": 1}
        async with httpx.AsyncClient(timeout=30.0, headers=headers) as client:
            resp = await client.get(NOMINATIM_URL, params=params)
            resp.raise_for_status()
            data = resp.json()
        if not data:
            return None
        # Nominatim отдаёт [south, north, west, east] строками
        south, north, west, east = (float(v) for v in data[0]["boundingbox"])
        return (south, west, north, east)

    def build_query(self, bbox: tuple[float, float, float, float], categories: list[str]) -> str:
        south, west, north, east = bbox
        box = f"{south},{west},{north},{east}"
        parts: list[str] = []
        for category in categories:
            for flt in osm_filters(category):
                for kind in ("node", "way"):
                    parts.append(f"  {kind}[{flt}]({box});")
        body = "\n".join(parts)
        return f"[out:json][timeout:90];\n(\n{body}\n);\nout center tags;"

    async def discover(self, area: Area, categories: list[str]) -> AsyncIterator[RawRecord]:
        bbox = area.bbox or await self.geocode(area.name)
        if bbox is None:
            return
        query = self.build_query(bbox, categories)
        if "node[" not in query:
            return
        payload = await self._post(query)
        for element in payload.get("elements", []):
            record = self.to_record(element)
            if record is not None:
                yield record

    async def _post(self, query: str) -> dict[str, Any]:
        url = self.settings.overpass_url
        headers = {"User-Agent": self.settings.user_agent}
        last_error: Exception | None = None
        async with httpx.AsyncClient(timeout=120.0, headers=headers) as client:
            for attempt in range(2):
                try:
                    resp = await client.post(url, data={"data": query})
                    resp.raise_for_status()
                    return resp.json()
                except (httpx.HTTPError, ValueError) as exc:  # noqa: PERF203
                    last_error = exc
                    # Overpass под нагрузкой отвечает 429/504; вторая попытка через паузу
                    await asyncio.sleep(5 * (attempt + 1))
        raise RuntimeError(f"Overpass недоступен: {last_error}")

    @staticmethod
    def to_record(element: dict[str, Any]) -> RawRecord | None:
        """Элемент Overpass -> RawRecord. Чистая функция, тестируется офлайн."""
        tags: dict[str, str] = element.get("tags") or {}
        name = tags.get("name") or tags.get("brand") or ""
        if not name.strip():
            # POI без названия для лид-листа бесполезен: это не бизнес, а объект
            return None
        center = element.get("center") or {}
        lat = element.get("lat", center.get("lat"))
        lon = element.get("lon", center.get("lon"))
        ref = f"{element.get('type', 'node')}/{element.get('id')}"

        facts = [
            Fact(
                field=field,
                value=tags[tag],
                source="osm",
                source_url=f"https://www.openstreetmap.org/{ref}",
                confidence=confidence,
                personal_data=pii,
            )
            for tag, (field, confidence, pii) in _TAG_FACTS.items()
            if tags.get(tag)
        ]
        street = " ".join(
            part for part in (tags.get("addr:street"), tags.get("addr:housenumber")) if part
        )
        if street:
            facts.append(
                Fact(field="street", value=street, source="osm", confidence=0.8)
            )
        for tag in _CATEGORY_TAGS:
            if tags.get(tag):
                facts.append(
                    Fact(field="category_raw", value=tags[tag], source="osm", confidence=0.7)
                )

        for cuisine in normalize_cuisine(tags.get("cuisine")):
            facts.append(Fact(field="cuisine", value=cuisine, source="osm", confidence=0.6))

        # Теги приёма заказов, которые OSM отдаёт даром — прямой вход в сигналы ops.*
        for tag in ("delivery", "takeaway", "reservation", "internet_access"):
            if tags.get(tag):
                facts.append(
                    Fact(field=f"osm_{tag}", value=tags[tag], source="osm", confidence=0.6)
                )

        return RawRecord(
            source="osm",
            source_url=f"https://www.openstreetmap.org/{ref}",
            ref=ref,
            name=name.strip(),
            facts=facts,
            lat=float(lat) if lat is not None else None,
            lon=float(lon) if lon is not None else None,
            raw=tags,
        )
