"""Full-width rows are captions or section markers, never column labels.

The reference contract's rate card marks both its title and its section rows as <th>. Counting
either as a header level renames every column after the section that happened to come first, and
loses the section row entirely.
"""

from __future__ import annotations

from tx.pipeline.flatten import derive_keys, flatten
from tx.pipeline.grid import parse_table

RATE_CARD = """
<table>
  <thead>
    <tr><th colspan="4">TABLE 1A - PERSONNEL RATES</th></tr>
    <tr><th>Trade / Skill or Position</th><th>Total Half Day</th>
        <th>Total Standard Day</th><th>Total Shift Day</th></tr>
    <tr><th colspan="4">CSR - Crane, Scaffold &amp; Rigging</th></tr>
  </thead>
  <tbody>
    <tr><td>CSR1 Crane Operator - C1 (Permanent)</td><td>$ 504.46</td>
        <td>$ 865.70</td><td>$ 1,100.46</td></tr>
    <tr><td>CSR2 Crane Operator - CN (Permanent)</td><td>$ 488.68</td>
        <td>$ 837.44</td><td>$ 1,064.04</td></tr>
  </tbody>
</table>
"""


def test_section_banner_does_not_become_a_header_level():
    grid = parse_table(RATE_CARD)
    assert grid.n_header_rows == 2
    assert derive_keys(grid.header()) == [
        "trade_skill_or_position",
        "total_half_day",
        "total_standard_day",
        "total_shift_day",
    ]


def test_leading_banner_is_read_as_the_title():
    assert parse_table(RATE_CARD).title == "TABLE 1A - PERSONNEL RATES"


def test_section_row_survives_as_data():
    grid = parse_table(RATE_CARD)
    body = grid.body()
    assert len(body) == 3
    assert body[0][0] == "CSR - Crane, Scaffold & Rigging"
    assert body[1][0].startswith("CSR1")


def test_flatten_keeps_the_section_row_and_titles_the_table():
    table = flatten("t_0001", RATE_CARD, [128], default_currency="AUD")
    assert table.title == "TABLE 1A - PERSONNEL RATES"
    assert table.n_rows == 3
    assert [c.key for c in table.columns][:2] == [
        "trade_skill_or_position",
        "total_half_day",
    ]
    assert table.rows[1]["total_half_day"]["value"] == 504.46


def test_a_section_row_does_not_fill_every_money_column():
    """Spanning repeats the label across the row; kept, it warns once per money column."""
    table = flatten("t_0001", RATE_CARD, [128], default_currency="AUD")
    section = table.rows[0]
    assert section["trade_skill_or_position"]["raw"] == "CSR - Crane, Scaffold & Rigging"
    assert section["total_half_day"]["raw"] == ""
    assert not [w for w in table.warnings if w.row_index == 0]


def test_a_banner_that_starts_after_an_empty_cell_is_still_a_banner():
    """A spanning cell placed in column 2 leaves column 1 empty, but it is the same thing."""
    html = (
        "<table><thead><tr><th>Code</th><th>A</th><th>B</th><th>C</th></tr></thead><tbody>"
        "<tr><td></td><td colspan='3'>Ferry Timetable</td></tr>"
        "<tr><td>X1</td><td>1.00</td><td>2.00</td><td>3.00</td></tr></tbody></table>"
    )
    grid = parse_table(html)
    assert grid.is_banner(1)
    table = flatten("t_0002", html, [1])
    assert table.rows[0]["code"]["raw"] == "Ferry Timetable"
    assert table.rows[0]["a"]["raw"] == ""


def test_a_row_repeating_one_rate_is_not_a_banner():
    """CSR13 charges $100.70 for every shift type. That is data, not a section marker."""
    html = (
        "<table><thead><tr><th>A</th><th>B</th><th>C</th><th>D</th></tr></thead><tbody>"
        "<tr><td>$ 100.70</td><td>$ 100.70</td><td>$ 100.70</td><td>$ 100.70</td></tr>"
        "</tbody></table>"
    )
    grid = parse_table(html)
    assert not grid.is_banner(1)


def test_a_lone_banner_row_is_still_usable_as_a_header():
    """A one-row header that happens to span must not leave the grid headerless."""
    html = "<table><thead><tr><th colspan='2'>Notes</th></tr></thead><tbody>"
    html += "<tr><td>a</td><td>b</td></tr></tbody></table>"
    grid = parse_table(html)
    assert grid.n_header_rows == 1
    assert len(grid.header()) == 2
