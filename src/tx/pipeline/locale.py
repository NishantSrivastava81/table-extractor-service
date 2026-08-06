"""Number parsing that refuses to guess.


"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from tx.core.config import settings

#: (thousands separator, decimal separator, label)
CANDIDATES: tuple[tuple[str, str, str], ...] = (
    (",", ".", "en"),
    (".", ",", "eu"),
    (" ", ",", "fr"),
    ("'", ".", "ch"),
)

_LOCALE_BY_HINT = {
    "en": "en",
    "en-au": "en",
    "en-us": "en",
    "en-gb": "en",
    "de": "eu",
    "de-de": "eu",
    "it": "eu",
    "it-it": "eu",
    "es": "eu",
    "es-es": "eu",
    "nl": "eu",
    "pt": "eu",
    "pt-br": "eu",
    "fr": "fr",
    "fr-fr": "fr",
    "ch": "ch",
    "de-ch": "ch",
}

CURRENCY_SYMBOLS = {"$": None, "€": "EUR", "£": "GBP", "¥": "JPY", "A$": "AUD", "US$": "USD"}
_ISO = re.compile(r"\b(AUD|USD|EUR|GBP|NZD|SGD|JPY|CAD|CHF|INR)\b")
_NUMERIC_CHARS = re.compile(r"[\d\s.,'\u00a0-]+")
_HAS_DIGIT = re.compile(r"\d")
#: A whole cell that is a number: digits, separators, an optional sign, currency mark or
#: percent, and nothing else. "4WD" and "3-6T" contain digits but are not numbers, and typing
#: their column as numeric makes every product name fail to parse.
_NUMERIC_LITERAL = re.compile(r"^[\s$\u20ac\u00a3\u00a5+\-(]*\d[\d\s.,'\u00a0]*\)?\s*%?\s*$")


def is_numeric_literal(text: str) -> bool:
    body = _ISO.sub("", text or "").strip()
    return bool(body) and bool(_NUMERIC_LITERAL.match(body))


@dataclass
class NumberFormat:
    label: str
    thousands: str
    decimal: str


def _strip_to_number(text: str) -> str:
    """Keep only the numeric run, dropping currency marks, percent signs and stray words."""
    cleaned = text.replace("\u00a0", " ").strip()
    cleaned = re.sub(r"^[^\d\-+]*", "", cleaned)
    cleaned = re.sub(r"[^\d\s.,'\-+]*$", "", cleaned)
    return cleaned.strip()


def _try_parse(text: str, fmt: NumberFormat) -> Decimal | None:
    body = _strip_to_number(text)
    if not body or not _HAS_DIGIT.search(body):
        return None
    if not _NUMERIC_CHARS.fullmatch(body):
        return None

    negative = body.startswith("-")
    body = body.lstrip("+-")

    if fmt.decimal in body:
        integer, _, fraction = body.rpartition(fmt.decimal)
        if fmt.decimal in integer or not fraction.isdigit():
            return None
    else:
        integer, fraction = body, ""

    integer = integer.replace(fmt.thousands, "").replace(" ", "")
    if not integer.isdigit() and integer != "":
        return None

    # A thousands separator must group in threes; "1,23" is not English.
    groups = [g for g in body.split(fmt.decimal)[0].split(fmt.thousands) if g != ""]
    if len(groups) > 1 and (len(groups[0]) > 3 or any(len(g) != 3 for g in groups[1:])):
        return None

    try:
        value = Decimal(f"{integer or '0'}.{fraction}" if fraction else (integer or "0"))
    except InvalidOperation:
        return None
    return -value if negative else value


def infer_format(values: list[str], hint: str | None = None) -> tuple[NumberFormat | None, bool]:
    """Return the column's number format and whether the column was ambiguous."""
    if hint:
        label = _LOCALE_BY_HINT.get(hint.strip().lower())
        if label:
            thousands, decimal, _ = next(c for c in CANDIDATES if c[2] == label)
            return NumberFormat(label, thousands, decimal), False

    # Placeholders such as '-' or 'N/A' say nothing about separators, so they get no vote.
    populated = [v for v in values if v and is_numeric_literal(v)]
    if not populated:
        return None, False

    # A convention has to explain most cells, not all: a single mistranscribed cell must not
    # veto the column and null every sound value in it. Stragglers are reported per cell.
    floor = settings.locale_min_agreement * len(populated)
    scored: list[tuple[int, NumberFormat]] = []
    for thousands, decimal, label in CANDIDATES:
        fmt = NumberFormat(label, thousands, decimal)
        hits = sum(_try_parse(v, fmt) is not None for v in populated)
        if hits >= floor:
            scored.append((hits, fmt))

    if not scored:
        return None, False

    best_score = max(score for score, _ in scored)
    leaders = [fmt for score, fmt in scored if score == best_score]
    first = leaders[0]
    if len(leaders) == 1:
        return first, False

    # Equally supported conventions that read any shared cell differently cannot be told apart.
    for other in leaders[1:]:
        for value in populated:
            a, b = _try_parse(value, first), _try_parse(value, other)
            if a is not None and b is not None and a != b:
                return None, True
    return first, False


def parse_number(text: str, fmt: NumberFormat | None) -> Decimal | None:
    if fmt is None:
        return None
    return _try_parse(text, fmt)


def repair_separators(text: str) -> Decimal | None:
    """Recover a value whose separators are wrong but whose digits are not in doubt.

    On a rasterised page the model writes '1.400.66' for '1,400.66'. Both readings of that
    string, English and European, give 1400.66, so no digit is being guessed. Only a trailing
    group of one or two digits qualifies: '1.234' really is ambiguous and is left alone.
    """
    body = _strip_to_number(text)
    if not body or not _NUMERIC_CHARS.fullmatch(body):
        return None
    negative = body.startswith("-")
    body = body.lstrip("+-")

    parts = re.split(r"[.,'\s\u00a0]+", body)
    if len(parts) < 3 or not all(p.isdigit() for p in parts):
        return None
    if not 1 <= len(parts[-1]) <= 2:
        return None
    if len(parts[0]) > 3 or any(len(p) != 3 for p in parts[1:-1]):
        return None

    try:
        value = Decimal(f"{''.join(parts[:-1])}.{parts[-1]}")
    except InvalidOperation:
        return None
    return -value if negative else value


def detect_currency(text: str, default: str | None) -> str | None:
    if match := _ISO.search(text or ""):
        return match.group(1)
    for symbol, code in CURRENCY_SYMBOLS.items():
        if symbol in (text or ""):
            return code or default
    return default


def looks_like_money(text: str) -> bool:
    """A currency mark alone is not enough: the cell has to be a number as well."""
    if not text:
        return False
    marked = any(s in text for s in CURRENCY_SYMBOLS) or bool(_ISO.search(text))
    return marked and is_numeric_literal(text)


def looks_like_percent(text: str) -> bool:
    return "%" in (text or "") and is_numeric_literal(text)
