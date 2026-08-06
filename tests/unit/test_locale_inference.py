"""Column-level number format inference.

Two failure modes matter here and they pull in opposite directions. Demanding that a convention
explain every cell lets one mistranscribed value reject all four candidates, which nulls a whole
column of sound prices. Accepting a convention too readily turns 1.234 into 1234.
"""

from __future__ import annotations

from decimal import Decimal

from tx.pipeline.locale import infer_format, parse_number, repair_separators

CLEAN = ["$ 865.70", "$ 1,100.46", "$ 1,270.57", "$ 1,448.62", "$ 2,026.68"]


def test_a_clean_english_column_resolves():
    fmt, ambiguous = infer_format(CLEAN)
    assert fmt is not None and fmt.label == "en"
    assert not ambiguous
    assert parse_number("$ 1,100.46", fmt) == Decimal("1100.46")


def test_one_mistranscribed_cell_does_not_null_the_column():
    """'1.029.05' is an OCR slip for '1,029.05'. It must not veto the other values."""
    fmt, ambiguous = infer_format([*CLEAN * 4, "$ 1.029.05"])
    assert fmt is not None and fmt.label == "en"
    assert not ambiguous
    assert parse_number("$ 865.70", fmt) == Decimal("865.70")
    assert parse_number("$ 1.029.05", fmt) is None


def test_placeholders_get_no_vote():
    fmt, ambiguous = infer_format([*CLEAN, "$ -", "-", "N/A"])
    assert fmt is not None and fmt.label == "en"
    assert not ambiguous


def test_a_genuinely_ambiguous_column_is_still_refused():
    """1.234 is 1234 in German and 1.234 in English, and the column cannot say which."""
    fmt, ambiguous = infer_format(["1.234", "5.678", "9.012"])
    assert fmt is None
    assert ambiguous


def test_a_european_column_resolves_to_european():
    fmt, _ = infer_format(["1.234,56", "9.876,54", "2.500,00"])
    assert fmt is not None and fmt.label == "eu"
    assert parse_number("1.234,56", fmt) == Decimal("1234.56")


def test_a_column_of_no_numbers_resolves_to_nothing():
    fmt, ambiguous = infer_format(["N/A", "-", "TBC"])
    assert fmt is None
    assert not ambiguous


def test_noise_beyond_the_threshold_still_refuses():
    """Majority support, not a plurality: mostly unparseable means the column is not understood."""
    fmt, _ = infer_format(["$ 865.70", "1.2.3.4", "5.6.7.8", "9.8.7.6", "4.3.2.1"])
    assert fmt is None


def test_a_misplaced_separator_is_recovered_because_no_digit_is_in_doubt():
    """'1.400.66' reads as 1400.66 under both English and European rules."""
    assert repair_separators("$ 1.400.66") == Decimal("1400.66")
    assert repair_separators("$ 2,394,46") == Decimal("2394.46")
    assert repair_separators("$ 1,549,40") == Decimal("1549.40")


def test_a_genuinely_ambiguous_string_is_not_repaired():
    """'1.234' is 1234 or 1.234 depending on locale, so guessing would change it 1000-fold."""
    assert repair_separators("1.234") is None
    assert repair_separators("1,234") is None


def test_repair_refuses_a_malformed_group():
    assert repair_separators("1.23.456") is None
    assert repair_separators("12345.6789.01") is None
