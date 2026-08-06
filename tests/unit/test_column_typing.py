"""Column typing: a column of product names is not a numeric column."""

from __future__ import annotations

from tx.pipeline.flatten import flatten
from tx.pipeline.locale import is_numeric_literal, looks_like_money

HEADER = "<tr><th>Description</th><th>Qty</th><th>Daily Rate</th><th>Mob</th></tr>"

# Verbatim from the reference contract: every description contains a digit.
ROWS = [
    ("Dual Cab Ute - 4WD", "1", "$ 93.00", ""),
    ("Dual Cab Ute - 2WD", "1", "$ 69.00", ""),
    ("Van - 12 Seater", "1", "$ 112.00", ""),
    ("Light Truck - Single Cab 3-6T", "1", "$ 107.00", ""),
    ("20T Franna", "1", "$ 353.00", "POA"),
    ("90/100T Rough Terrain Crane", "1", "$ 894.00", "POA"),
]


def build() -> str:
    body = "".join(
        f"<tr><td>{d}</td><td>{q}</td><td>{r}</td><td>{m}</td></tr>" for d, q, r, m in ROWS
    )
    return f"<table><thead>{HEADER}</thead><tbody>{body}</tbody></table>"


def test_numeric_literal_requires_the_whole_cell_to_be_a_number():
    assert is_numeric_literal("$ 93.00")
    assert is_numeric_literal("1,234.56")
    assert is_numeric_literal("5%")
    assert not is_numeric_literal("Dual Cab Ute - 4WD")
    assert not is_numeric_literal("3-6T")
    assert not is_numeric_literal("20T Franna")
    assert not is_numeric_literal("POA")


def test_currency_mark_alone_does_not_make_a_cell_money():
    assert looks_like_money("$ 93.00")
    assert not looks_like_money("Priced in $ per tonne")


def test_description_column_full_of_model_numbers_is_still_text():
    table = flatten("t_1", build(), [130], default_currency="AUD")
    by_key = {c.key: c for c in table.columns}
    assert by_key["description"].type == "string"
    assert by_key["qty"].type == "number"
    assert by_key["daily_rate"].type == "money"
    assert by_key["mob"].type == "string"


def test_text_values_survive_and_raise_no_warnings():
    table = flatten("t_1", build(), [130], default_currency="AUD")
    assert table.rows[0]["description"]["value"] == "Dual Cab Ute - 4WD"
    assert table.rows[4]["description"]["value"] == "20T Franna"
    assert [w.code for w in table.warnings] == [], "product names must not be parse failures"


def test_money_still_parses_alongside_them():
    table = flatten("t_1", build(), [130], default_currency="AUD")
    assert table.rows[0]["daily_rate"]["value"] == 93.0
    assert table.rows[0]["daily_rate"]["currency"] == "AUD"
