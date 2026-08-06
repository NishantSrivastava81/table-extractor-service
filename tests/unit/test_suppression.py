"""Suppression: which extracted tables are page furniture rather than content."""

from __future__ import annotations

from tx.pipeline.orchestrator import _is_furniture


def test_running_header_block_is_furniture():
    """The QCLNG letterhead block: 3 columns, 2 rows, on nearly every page."""
    assert _is_furniture(rows=2, cols=3)


def test_single_cell_note_is_furniture():
    assert _is_furniture(rows=1, cols=2)


def test_tall_single_column_list_is_content():
    """A 181-row equipment list has one column and is not furniture."""
    assert not _is_furniture(rows=181, cols=1)
    assert not _is_furniture(rows=57, cols=1)


def test_short_single_column_block_is_still_furniture():
    assert _is_furniture(rows=4, cols=1)


def test_ordinary_table_is_content():
    assert not _is_furniture(rows=40, cols=7)
