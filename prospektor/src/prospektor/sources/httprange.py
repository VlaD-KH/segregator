"""Чтение удалённого parquet HTTP range-запросами.

Запасной путь к Overture для сред, где DuckDB не может доставить расширение
`httpfs`: сам бакет доступен, а `extensions.duckdb.org` закрыт. Ровно так
выглядела среда, в которой снимались данные Старгарда и Щецина.

Оказалось, что путь не только запасной, но и дешёвый: parquet Overture несёт
статистику по `bbox` в каждой row group, а группы упорядочены пространственно.
Поэтому на город уходит один файл из шестнадцати и одна-две группы из 256 —
около 30 МБ вместо 10 ГБ. Зависимость всего одна, pyarrow, и та опциональная.
"""

from __future__ import annotations

import io
from collections.abc import Iterator
from dataclasses import dataclass, field

import httpx


class HttpRangeFile(io.RawIOBase):
    """Файловый объект поверх Range-запросов.

    pyarrow умеет читать из любого объекта с ``seek`` и ``read``, поэтому
    полноценная файловая система не нужна — хватает этих двух методов.
    """

    def __init__(self, url: str, client: httpx.Client, timeout: float = 180.0) -> None:
        self.url = url
        self.client = client
        self.timeout = timeout
        self._pos = 0
        self.bytes_read = 0
        self.requests = 0
        head = client.head(url, timeout=timeout)
        head.raise_for_status()
        self.size = int(head.headers["content-length"])

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._pos

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        if whence == io.SEEK_SET:
            self._pos = offset
        elif whence == io.SEEK_CUR:
            self._pos += offset
        else:
            self._pos = self.size + offset
        return self._pos

    def read(self, size: int = -1) -> bytes:  # type: ignore[override]
        if size is None or size < 0:
            size = self.size - self._pos
        if size == 0 or self._pos >= self.size:
            return b""
        end = min(self._pos + size, self.size) - 1
        resp = self.client.get(
            self.url, headers={"Range": f"bytes={self._pos}-{end}"}, timeout=self.timeout
        )
        resp.raise_for_status()
        data = resp.content
        self._pos += len(data)
        self.bytes_read += len(data)
        self.requests += 1
        return data

    def readinto(self, buffer) -> int:  # type: ignore[override]
        data = self.read(len(buffer))
        buffer[: len(data)] = data
        return len(data)


@dataclass
class ScanStats:
    """Сколько стоила выборка. Нужна, чтобы стоимость была видна, а не угадывалась."""

    files_seen: int = 0
    files_touched: int = 0
    row_groups_read: int = 0
    rows: int = 0
    bytes_read: int = 0
    urls: list[str] = field(default_factory=list)

    @property
    def megabytes(self) -> float:
        return round(self.bytes_read / 1e6, 1)


def bbox_overlaps(
    stats_min_x: float, stats_max_x: float, stats_min_y: float, stats_max_y: float,
    west: float, south: float, east: float, north: float,
) -> bool:
    """Пересекается ли диапазон row group с искомым прямоугольником."""
    return not (
        stats_max_x < west or stats_min_x > east or stats_max_y < south or stats_min_y > north
    )


def scan_parquet_bbox(
    urls: list[str],
    bbox: tuple[float, float, float, float],
    columns: list[str],
    client: httpx.Client,
    stats: ScanStats | None = None,
) -> Iterator[dict]:
    """Прочитать строки внутри bbox из набора удалённых parquet-файлов.

    ``bbox`` задаётся как (south, west, north, east) — тот же порядок, что у
    :class:`~prospektor.sources.base.Area`.
    """
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    south, west, north, east = bbox
    stats = stats if stats is not None else ScanStats()

    for url in urls:
        stats.files_seen += 1
        handle = HttpRangeFile(url, client)
        parquet = pq.ParquetFile(handle)
        meta = parquet.metadata
        paths = [meta.schema.column(i).path for i in range(meta.num_columns)]
        try:
            ix, iy = paths.index("bbox.xmin"), paths.index("bbox.ymin")
        except ValueError:  # pragma: no cover — формат Overture без bbox
            stats.bytes_read += handle.bytes_read
            continue

        hits = []
        for group in range(meta.num_row_groups):
            row_group = meta.row_group(group)
            sx, sy = row_group.column(ix).statistics, row_group.column(iy).statistics
            if sx is None or sy is None:  # pragma: no cover — parquet без статистики
                hits.append(group)
                continue
            if bbox_overlaps(sx.min, sx.max, sy.min, sy.max, west, south, east, north):
                hits.append(group)

        if hits:
            stats.files_touched += 1
            stats.row_groups_read += len(hits)
            stats.urls.append(url)
            table = parquet.read_row_groups(hits, columns=columns)
            xmin = pc.struct_field(table["bbox"], "xmin")
            ymin = pc.struct_field(table["bbox"], "ymin")
            mask = pc.and_(
                pc.and_(pc.greater_equal(xmin, west), pc.less_equal(xmin, east)),
                pc.and_(pc.greater_equal(ymin, south), pc.less_equal(ymin, north)),
            )
            selected = table.filter(mask)
            stats.rows += selected.num_rows
            yield from selected.to_pylist()

        stats.bytes_read += handle.bytes_read
