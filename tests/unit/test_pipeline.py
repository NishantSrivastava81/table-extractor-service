"""The pipeline logic that carries a measured decision."""

from __future__ import annotations

from tx.core.config import settings
from tx.pipeline.flatten import derive_keys, flatten
from tx.pipeline.grid import parse_table, top_level_tables
from tx.pipeline.locale import infer_format, parse_number
from tx.pipeline.merge import Fragment, merge_fragments, stitch
from tx.pipeline.reconcile import build_vocabulary, reconcile_html
from tx.pipeline.router import short_line_count, strips_for
from tx.pipeline.validate import check

HEADER = "<tr><th>Item</th><th>Desc</th><th>Qty</th><th>Amount</th></tr>"


def frag(rows: str) -> str:
    return f"<table><thead>{HEADER}</thead><tbody>{rows}</tbody></table>"


# ---------------------------------------------------------------- grid


def test_spans_are_expanded_so_every_column_has_a_value():
    html = (
        "<table><thead>"
        "<tr><th rowspan='2'>Item</th><th colspan='2'>Pricing</th></tr>"
        "<tr><th>Qty</th><th>Price</th></tr></thead>"
        "<tbody><tr><td>010</td><td>5</td><td>1.50</td></tr></tbody></table>"
    )
    grid = parse_table(html)
    assert grid.n_cols == 3
    assert grid.n_header_rows == 2
    assert grid.rows[0][0] == "Item" and grid.rows[1][0] == "Item"
    assert grid.header() == ["Item", "Pricing / Qty", "Pricing / Price"]


def test_nested_table_is_lifted_out_and_kept_against_its_cell():
    html = (
        "<table><tbody><tr><td>Scaffolding</td>"
        "<td><table><tr><th>Tier</th><th>Rate</th></tr>"
        "<tr><td>T1</td><td>26.25</td></tr></table></td>"
        "</tr></tbody></table>"
    )
    grid = parse_table(html)
    assert grid.nested, "nested table must be retained for the explode step"
    assert "Scaffolding" in grid.rows[0][0]


def test_top_level_split_ignores_nested_tables():
    html = "<table><tr><td><table><tr><td>x</td></tr></table></td></tr></table><table><tr><td>y</td></tr></table>"
    assert len(top_level_tables(html)) == 2


# ---------------------------------------------------------------- router


def test_strip_count_scales_with_rows_and_is_capped():
    assert strips_for(3) == 1
    assert strips_for(85) == 4
    assert strips_for(10_000) == settings.max_strips


def test_short_line_density_sees_a_list_table():
    """The signal that rescued a 183-row list OCR reported as a 2x2 table."""
    listy = "\n".join(f"Tool number {i}" for i in range(60))
    prose = "This agreement is made between the parties on the date first written above. " * 20
    assert short_line_count(listy) >= 30
    assert short_line_count(prose) < 30


# ---------------------------------------------------------------- reconcile


def test_number_vocabulary_does_not_swallow_the_gap_between_cells():
    """'5 838.13' is two values. Treating it as one loses 838.13 from the vocabulary."""
    _, _, seen = build_vocabulary({1: "<td>5 838.13</td><td>1,064.72</td>"})
    assert "83813" in seen
    assert "583813" not in seen


def test_near_miss_identifier_is_repaired_to_the_ocr_reading():
    pages = {1: "Item 010 Mat.No.: ALRD00882 qty 6,000"}
    html, report = reconcile_html("<table><tr><td>ALU000892</td></tr></table>", pages)
    assert "ALRD00882" in html
    assert report.repaired == 1


def test_ambiguous_identifier_is_blanked_rather_than_coin_flipped():
    pages = {1: "codes ABCD00001 and ABCD00002 appear here"}
    _, report = reconcile_html("<table><tr><td>ABCD00003</td></tr></table>", pages)
    assert report.blanked == 1 and report.repaired == 0


def test_ungrounded_number_is_reported_but_kept():
    """Deleting these emptied 107 correct price cells on the reference contract."""
    pages = {1: "the only figure here is 93.00"}
    html, report = reconcile_html("<table><tr><td>$ 2,394.00</td></tr></table>", pages)
    assert "2,394.00" in html
    assert report.numbers_ungrounded == 1


# ---------------------------------------------------------------- merge


def test_stitch_drops_a_repeated_header():
    a = frag("<tr><td>010</td><td>x</td><td>5</td><td>10.00</td></tr>")
    b = frag("<tr><td>020</td><td>y</td><td>6</td><td>12.00</td></tr>")
    merged = parse_table(stitch(a, b))
    assert [r[0] for r in merged.body()] == ["010", "020"]
    assert merged.rows.count(["Item", "Desc", "Qty", "Amount"]) == 1


def test_stitch_rejoins_a_cell_split_across_the_page_break():
    a = frag("<tr><td>010</td><td>Round bar</td><td>5</td><td>10.00</td></tr>")
    b = "<table><tbody><tr><td></td><td>Mill Length EN 755-2</td><td></td><td></td></tr></tbody></table>"
    merged = parse_table(stitch(a, b))
    assert len(merged.body()) == 1
    assert merged.body()[0][1] == "Round bar Mill Length EN 755-2"


def test_adjacent_sections_with_different_titles_are_not_welded_together():
    def titled(title: str, rows: str) -> str:
        return f"<table><thead><tr><th colspan='4'>{title}</th></tr>{HEADER}</thead><tbody>{rows}</tbody></table>"

    a = Fragment(
        "a",
        titled("Delivery - Hamburg", "<tr><td>010</td><td>x</td><td>5</td><td>1.00</td></tr>"),
        1,
    )
    b = Fragment(
        "b",
        titled("Delivery - Rotterdam", "<tr><td>010</td><td>y</td><td>6</td><td>2.00</td></tr>"),
        2,
    )
    merged, _ = merge_fragments([a, b])
    assert len(merged) == 2


# ---------------------------------------------------------------- locale


def test_english_and_european_groupings_both_parse():
    fmt, ambiguous = infer_format(["1,234.56", "2,500.00"])
    assert not ambiguous and float(parse_number("1,234.56", fmt)) == 1234.56

    fmt, ambiguous = infer_format(["1.234,56", "2.500,00"])
    assert not ambiguous and float(parse_number("1.234,56", fmt)) == 1234.56


def test_genuinely_ambiguous_column_refuses_to_guess():
    """1.234 is 1234 in German and 1.234 in English. Guessing is a 1000x error."""
    fmt, ambiguous = infer_format(["1.234", "2.500", "3.750"])
    assert ambiguous and fmt is None


# ---------------------------------------------------------------- flatten


def test_column_keys_are_stable_and_deduplicated():
    assert derive_keys(["Trade / Skill", "Day", "Day", ""]) == [
        "trade_skill",
        "day",
        "day_2",
        "col_3",
    ]


def test_nested_table_is_exploded_into_the_parent():
    html = (
        "<table><thead><tr><th>Service</th><th>Rate Tiers</th></tr></thead><tbody>"
        "<tr><td>Scaffolding</td>"
        "<td><table><thead><tr><th>Tier</th><th>Price</th></tr></thead><tbody>"
        "<tr><td>T1</td><td>26.25</td></tr><tr><td>T2</td><td>24.10</td></tr>"
        "</tbody></table></td></tr></tbody></table>"
    )
    table = flatten("t_1", html, [1], default_currency="AUD")
    keys = {c.key for c in table.columns}
    assert "rate_tiers__tier" in keys and "rate_tiers__price" in keys
    assert table.n_rows == 2, "one output row per child row"
    assert table.rows_before_explosion == 1
    assert all(r["service"]["raw"] == "Scaffolding" for r in table.rows), "parent repeats"
    assert [r["rate_tiers__tier"]["raw"] for r in table.rows] == ["T1", "T2"]
    assert table.rows[1]["_nest_path"]["raw"] == "rate_tiers[1]"


def test_money_is_typed_and_raw_is_always_kept():
    html = frag(
        "<tr><td>010</td><td>Bar</td><td>5</td><td>$ 1,234.56</td></tr>"
        "<tr><td>020</td><td>Rod</td><td>6</td><td>$ 2,500.00</td></tr>"
    )
    table = flatten("t_1", html, [1], default_currency="AUD")
    amount = next(c for c in table.columns if c.key == "amount")
    assert amount.type == "money" and amount.currency == "AUD"
    assert table.rows[0]["amount"]["value"] == 1234.56
    assert table.rows[0]["amount"]["raw"] == "$ 1,234.56"


# ---------------------------------------------------------------- verbatim


def test_verbatim_guard_flags_a_value_that_is_not_in_the_source():
    report = check(
        ["<table><tr><td>ALRD00882</td><td>2,394.00</td></tr></table>"], {1: "ALRD00882 93.00"}
    )
    assert "2,394.00" in report.ungrounded_numbers
    assert not report.invented_identifiers
    assert report.score < 1.0
