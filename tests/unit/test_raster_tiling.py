"""Rasterised pages are tiled to a rows-per-strip target, not to a strip count.

Measured on page 128 of the reference contract (85 estimated rows): 4 strips, 21 rows each,
scored 29-32 of 36 hand-checked money cells across three runs. 8 strips, 11 rows each, scored
35-36. Several errors at 21 rows a strip repeated in every run, so voting across runs cannot
fix them; only a better image can.

The control has to be rows per strip. Every strip is normalised to the same visual-token
budget, so a cap on strips per page would silently raise rows per strip on dense pages, which
is the wrong direction on exactly the pages that can least afford it.
"""

from __future__ import annotations

import math

from tx.core.config import settings
from tx.pipeline.ocr import OcrResult, OcrTable
from tx.pipeline.router import plan, strips_for


def rate_card(pages: int = 1, rows: int = 80) -> OcrResult:
    return OcrResult(
        n_pages=pages,
        page_texts={p: "" for p in range(1, pages + 1)},
        tables=[OcrTable(html="", pages=[1], n_rows=rows, n_cols=13)],
    )


def test_a_raster_page_gets_more_strips_than_a_text_page():
    ocr = rate_card()
    assert plan(ocr, raster_pages={1})[0].strips > plan(ocr)[0].strips


def test_the_measured_page_reproduces_the_measured_tiling():
    """Page 128: 85 rows, 4 strips normally, 8 as a raster. That is the pair that was scored."""
    ocr = rate_card(rows=85)
    assert plan(ocr)[0].strips == 4
    assert plan(ocr, raster_pages={1})[0].strips == 8


def test_a_dense_raster_page_keeps_its_rows_per_strip():
    """The point of the change: density must not degrade as a page gets longer."""
    for rows in (85, 150, 200, 260):
        assert rows / strips_for(rows, raster=True) <= settings.rows_per_strip_raster


def test_the_guard_sits_above_any_page_seen_so_far():
    """The densest page in the reference contract estimates 85 rows. Leave room well past it."""
    assert strips_for(85 * 2, raster=True) < settings.max_strips


def test_strip_count_is_still_bounded():
    assert strips_for(10_000, raster=True) == settings.max_strips


def test_strips_follow_the_target_exactly():
    rows = 200
    assert strips_for(rows, raster=True) == math.ceil(rows / settings.rows_per_strip_raster)


def test_a_page_not_marked_raster_is_unchanged():
    ocr = rate_card()
    assert plan(ocr, raster_pages={99})[0].strips == plan(ocr)[0].strips


def test_matching_the_targets_disables_the_behaviour(monkeypatch):
    monkeypatch.setattr(settings, "rows_per_strip_raster", settings.rows_per_strip)
    assert strips_for(85, raster=True) == strips_for(85, raster=False)


def test_the_reason_records_why_the_page_was_tiled_finely():
    assert "raster" in plan(rate_card(), raster_pages={1})[0].reason
