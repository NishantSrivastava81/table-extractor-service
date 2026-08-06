"""A headerless table must not lose its first row.

The model often returns a lettered list from a contract clause as a two-column table with no
<thead>. Promoting row 0 to a header deleted item (A) of a twelve-item list on page 92 of the
reference contract, and left the column named after it.

The asymmetry decides the default: a spurious header row is visible and recoverable, a deleted
row is neither. So row 0 is kept as data whenever the first column counts up from it.
"""

from __future__ import annotations

from tx.pipeline.flatten import flatten
from tx.pipeline.grid import parse_table

LETTERED = """
<table>
  <tr><td>(A)</td><td>Pipe work pre-test checklist.</td></tr>
  <tr><td>(B)</td><td>Test blind certificates.</td></tr>
  <tr><td>(C)</td><td>Construction punch List.</td></tr>
  <tr><td>(D)</td><td>Approval to test form.</td></tr>
</table>
"""

NUMBERED = """
<table>
  <tr><td>1.</td><td>Mobilise</td><td>5</td></tr>
  <tr><td>2.</td><td>Commission</td><td>9</td></tr>
  <tr><td>3.</td><td>Handover</td><td>2</td></tr>
</table>
"""

WITH_HEADER = """
<table>
  <tr><td>Item</td><td>Description</td></tr>
  <tr><td>1</td><td>Mobilise</td></tr>
  <tr><td>2</td><td>Commission</td></tr>
  <tr><td>3</td><td>Handover</td></tr>
</table>
"""


def test_a_lettered_list_keeps_its_first_item():
    grid = parse_table(LETTERED)
    assert grid.n_header_rows == 0
    assert len(grid.body()) == 4
    assert grid.body()[0][0] == "(A)"


def test_columns_are_positional_when_there_is_no_header():
    """Naming a column after a data row hides that the row is also in the body."""
    table = flatten("t_0005", LETTERED, [92])
    assert [c.key for c in table.columns] == ["col_0", "col_1"]


def test_the_first_item_survives_into_the_flattened_rows():
    table = flatten("t_0005", LETTERED, [92])
    assert table.n_rows == 4
    first = table.rows[0]
    assert any(
        cell["raw"] == "Pipe work pre-test checklist."
        for cell in first.values()
        if isinstance(cell, dict)
    )


def test_a_numbered_list_keeps_its_first_item():
    grid = parse_table(NUMBERED)
    assert grid.n_header_rows == 0
    assert grid.body()[0][1] == "Mobilise"


def test_a_real_header_is_still_recognised():
    """'Item' is not an enumerator, so the sequence does not start at row 0."""
    grid = parse_table(WITH_HEADER)
    assert grid.n_header_rows == 1
    assert grid.header() == ["Item", "Description"]
    assert len(grid.body()) == 3


def test_an_explicit_header_above_an_enumeration_is_respected():
    html = "<table><thead><tr><th>Ref</th><th>Task</th></tr></thead><tbody>"
    html += "<tr><td>(A)</td><td>x</td></tr><tr><td>(B)</td><td>y</td></tr>"
    html += "<tr><td>(C)</td><td>z</td></tr></tbody></table>"
    grid = parse_table(html)
    assert grid.n_header_rows == 1
    assert len(grid.body()) == 3


def test_a_non_consecutive_first_column_is_not_an_enumeration():
    """Item codes are not a count, so they say nothing about whether row 0 is a header."""
    html = "<table><tr><td>Code</td><td>Rate</td></tr>"
    html += "<tr><td>7</td><td>1.00</td></tr><tr><td>19</td><td>2.00</td></tr>"
    html += "<tr><td>44</td><td>3.00</td></tr></table>"
    assert parse_table(html).n_header_rows == 1
