"""Repeating page furniture must not survive into the merge stage."""

from __future__ import annotations

from tx.pipeline.merge import Fragment
from tx.pipeline.orchestrator import drop_repeating_furniture

LETTERHEAD = (
    "<table><tr><td>QCLNG OPERATING COMPANY PTY LTD</td><td>CONTRACT No.</td></tr>"
    "<tr><td>Provisions of Maintenance Services</td><td>CW895642</td></tr></table>"
)


def real_table(page: int) -> str:
    return (
        "<table><thead><tr><th>Item</th><th>Amount</th></tr></thead><tbody>"
        f"<tr><td>{page}01</td><td>10.00</td></tr>"
        f"<tr><td>{page}02</td><td>20.00</td></tr>"
        f"<tr><td>{page}03</td><td>30.00</td></tr></tbody></table>"
    )


def test_letterhead_repeated_across_pages_is_dropped():
    """Eighteen two-row copies would otherwise merge into an eighteen-row 'table'."""
    fragments = [Fragment(f"p{p}_0", LETTERHEAD, p) for p in range(1, 19)]
    kept, dropped = drop_repeating_furniture(fragments)
    assert kept == []
    assert dropped == 18


def test_real_tables_are_never_dropped():
    fragments = [Fragment(f"p{p}_0", real_table(p), p) for p in range(1, 19)]
    kept, dropped = drop_repeating_furniture(fragments)
    assert len(kept) == 18 and dropped == 0


def test_a_short_block_appearing_once_is_kept():
    """Only repetition marks furniture. A one-off small table may be a continuation."""
    fragments = [Fragment("p1_0", LETTERHEAD, 1)]
    kept, dropped = drop_repeating_furniture(fragments)
    assert len(kept) == 1 and dropped == 0


def test_furniture_is_removed_but_content_on_the_same_pages_survives():
    fragments = []
    for page in range(1, 11):
        fragments.append(Fragment(f"p{page}_0", LETTERHEAD, page))
        fragments.append(Fragment(f"p{page}_1", real_table(page), page))
    kept, dropped = drop_repeating_furniture(fragments)
    assert dropped == 10
    assert {f.fragment_id for f in kept} == {f"p{p}_1" for p in range(1, 11)}
