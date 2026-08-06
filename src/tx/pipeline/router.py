"""Which pages earn a vision call, and how finely each needs slicing.

The routing is free: the OCR leg has already run. Skipping a page saves its whole cost, so this
is the largest cost lever in the service. On the reference contract it sent 69 of 240 pages,
which is 136 images instead of 960.

It is deliberately generous. Table detection alone reported a 2x3 table on one page and 2x2 on
another; those pages actually held 59-row and 183-row lists. A page wrongly skipped is data lost
silently, so a second signal on line shape backs up the detector.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from tx.core.config import settings
from tx.pipeline.ocr import OcrResult

_SPLIT = re.compile(r"<tr>|\n")
_TAGS = re.compile(r"<[^>]+>")


@dataclass
class PagePlan:
    page: int
    strips: int
    reason: str
    estimated_rows: int


def short_line_count(text: str) -> int:
    parts = (p.strip() for p in _SPLIT.split(text or "") if p.strip())
    return sum(1 for p in parts if 2 < len(_TAGS.sub("", p)) < settings.router_max_line_len)


def strips_for(rows: int, raster: bool = False) -> int:
    per_strip = settings.rows_per_strip_raster if raster else settings.rows_per_strip
    return max(1, min(settings.max_strips, -(-rows // per_strip)))


def plan(
    ocr: OcrResult,
    allowed_pages: set[int] | None = None,
    mode: str = "balanced",
    raster_pages: set[int] | None = None,
) -> list[PagePlan]:
    if mode == "fast":
        return []

    raster_pages = raster_pages or set()

    rows_on: dict[int, int] = {}
    reason: dict[int, str] = {}

    for table in ocr.tables:
        if (
            table.n_rows < settings.router_min_table_rows
            or table.n_cols < settings.router_min_table_cols
        ):
            continue
        for page in table.pages:
            if table.n_rows > rows_on.get(page, 0):
                rows_on[page] = table.n_rows
                reason[page] = "table detected"

    # Detection misses list-shaped and rasterised tables, so fall back to line shape.
    for page, text in ocr.page_texts.items():
        if page in rows_on:
            continue
        short = short_line_count(text)
        if short >= settings.router_min_short_lines:
            rows_on[page] = short
            reason[page] = "line density"

    if mode == "thorough":
        for page in ocr.page_texts:
            rows_on.setdefault(page, settings.rows_per_strip)
            reason.setdefault(page, "thorough mode")

    pages = sorted(rows_on)
    if allowed_pages is not None:
        pages = [p for p in pages if p in allowed_pages]

    return [
        PagePlan(
            page=page,
            strips=strips_for(rows_on[page], page in raster_pages),
            reason=reason[page] + (" (raster)" if page in raster_pages else ""),
            estimated_rows=rows_on[page],
        )
        for page in pages
    ]
