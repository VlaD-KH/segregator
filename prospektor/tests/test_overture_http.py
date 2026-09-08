"""Запасной режим Overture: чтение parquet HTTP range-запросами.

Тест офлайновый: parquet генерируется на месте и отдаётся через подставной
HTTP-клиент. Проверяется главное — что лишние row groups не читаются. Именно
на этом отсечении держится вся экономика выборки: 30 МБ на город вместо 10 ГБ.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

pytest.importorskip("pyarrow", reason="запасной режим ставится отдельно: .[overture-http]")

import pyarrow as pa  # noqa: E402
import pyarrow.parquet as pq  # noqa: E402

from prospektor.sources.httprange import ScanStats, bbox_overlaps, scan_parquet_bbox  # noqa: E402
from prospektor.sources.overture import OvertureSource  # noqa: E402


def _make_parquet(path: Path) -> None:
    """Три row group: западная, целевая (Щецин) и восточная."""
    groups = [
        [("far west", 14.0, 53.4), ("far west 2", 14.05, 53.41)],
        [("Salon Szczecin", 14.55, 53.43), ("Barber Szczecin", 14.56, 53.44)],
        [("far east", 16.0, 53.4), ("far east 2", 16.05, 53.41)],
    ]
    writer = None
    for group in groups:
        table = pa.table({
            "id": [f"id-{n}" for n, _, _ in group],
            "names": [{"primary": n} for n, _, _ in group],
            "categories": [{"primary": "beauty_salon", "alternate": ["spas"]} for _ in group],
            "confidence": [0.9] * len(group),
            "websites": [["https://example.pl"]] * len(group),
            "phones": [["+48111222333"]] * len(group),
            "emails": [None] * len(group),
            "socials": [None] * len(group),
            "addresses": [[{"freeform": "Testowa 1", "locality": "Szczecin",
                            "postcode": "70-001", "country": "PL"}] for _ in group],
            "bbox": [{"xmin": x, "xmax": x, "ymin": y, "ymax": y} for _, x, y in group],
        })
        if writer is None:
            writer = pq.ParquetWriter(path, table.schema)
        writer.write_table(table)
    assert writer is not None
    writer.close()


@pytest.fixture
def remote(tmp_path: Path) -> tuple[str, httpx.Client]:
    """Локальный parquet, отданный через подставной транспорт с поддержкой Range."""
    path = tmp_path / "places.parquet"
    _make_parquet(path)
    blob = path.read_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "HEAD":
            return httpx.Response(200, headers={"content-length": str(len(blob))})
        start, end = request.headers["Range"].removeprefix("bytes=").split("-")
        return httpx.Response(200, content=blob[int(start) : int(end) + 1])

    return "https://example.test/places.parquet", httpx.Client(
        transport=httpx.MockTransport(handler)
    )


def test_читаются_только_нужные_row_groups(remote) -> None:
    """Ради этого запасной режим и существует."""
    url, client = remote
    stats = ScanStats()
    rows = list(
        scan_parquet_bbox([url], (53.32, 14.40, 53.53, 14.80), ["names", "bbox"], client, stats)
    )
    assert [r["names"]["primary"] for r in rows] == ["Salon Szczecin", "Barber Szczecin"]
    # Из трёх групп прочитана одна: западная и восточная отсечены по статистике
    assert stats.row_groups_read == 1
    assert stats.files_touched == 1
    assert stats.rows == 2


def test_файл_вне_области_не_читается_вовсе(remote) -> None:
    url, client = remote
    stats = ScanStats()
    rows = list(scan_parquet_bbox([url], (40.0, 2.0, 41.0, 3.0), ["names", "bbox"], client, stats))
    assert rows == []
    assert stats.files_touched == 0
    assert stats.row_groups_read == 0


def test_строка_приводится_к_виду_адаптера(remote) -> None:
    """Оба режима должны отдавать одинаковую форму строки, иначе to_record сломается."""
    url, client = remote
    rows = list(
        scan_parquet_bbox(
            [url], (53.32, 14.40, 53.53, 14.80),
            ["id", "names", "categories", "confidence", "websites", "phones", "emails",
             "socials", "addresses", "bbox"],
            client,
        )
    )
    flat = OvertureSource._flatten(rows[0])
    record = OvertureSource.to_record(flat)
    assert record is not None
    assert record.name == "Salon Szczecin"
    assert record.lat == pytest.approx(53.43)
    поля = {f.field: f.value for f in record.facts}
    assert поля["city"] == "Szczecin"
    assert поля["website"] == "https://example.pl"
    # alternate попадает отдельным фактом — по нему находится половина салонов
    assert "spas" in {f.value for f in record.facts if f.field == "category_raw"}


def test_фильтр_по_категории_на_нашей_стороне(remote) -> None:
    """В режиме http фильтровать некому, кроме нас самих."""
    url, client = remote
    rows = list(
        scan_parquet_bbox([url], (53.32, 14.40, 53.53, 14.80), ["categories", "bbox"], client)
    )
    подходит = OvertureSource._row_categories(rows[0])
    assert подходит == {"beauty_salon", "spas"}
    assert подходит & OvertureSource._overture_categories(["kosmetyczka"])
    assert not подходит & OvertureSource._overture_categories(["pizzeria"])


@pytest.mark.parametrize(
    ("rg", "ожидание"),
    [
        ((14.0, 14.2, 53.0, 53.2), False),   # западнее и южнее
        ((14.5, 14.6, 53.4, 53.5), True),    # внутри
        ((14.3, 14.9, 53.2, 53.6), True),    # шире области
        ((16.0, 16.5, 53.4, 53.5), False),   # восточнее
    ],
)
def test_пересечение_считается_правильно(rg, ожидание) -> None:
    assert bbox_overlaps(*rg, 14.40, 53.32, 14.80, 53.53) is ожидание


def test_стоимость_выборки_видна(remote) -> None:
    """Сколько скачано — не догадка, а измеренное число."""
    url, client = remote
    stats = ScanStats()
    list(scan_parquet_bbox([url], (53.32, 14.40, 53.53, 14.80), ["names", "bbox"], client, stats))
    assert stats.bytes_read > 0
    assert stats.megabytes == round(stats.bytes_read / 1e6, 1)
    assert stats.urls == [url]
