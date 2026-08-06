"""The verbatim guard.

Assert that every identifier and number in the output actually appears in the OCR text. Anything
that does not was generated rather than read. This costs nothing at inference time and catches
the exact failure class that structural metrics score as near-perfect.

It reports. It does not edit. Deleting values a second reader could not confirm destroyed 107
correct prices on the reference contract, so the finding is surfaced and the value is kept.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from tx.pipeline.grid import extract_identifiers, table_text

NUMBER = re.compile(r"[-+]?\d[\d,.]*")


def _canon(value: str) -> str:
    return re.sub(r"[\s\u00a0]+", "", value or "").upper()


def _canon_number(value: str) -> str:
    digits = re.sub(r"[^\d.]", "", value or "")
    if "." in digits:
        digits = digits.rstrip("0").rstrip(".")
    return digits or "0"


@dataclass
class VerbatimReport:
    checked: int = 0
    grounded: int = 0
    invented_identifiers: list[str] = field(default_factory=list)
    ungrounded_numbers: list[str] = field(default_factory=list)

    @property
    def score(self) -> float:
        return self.grounded / self.checked if self.checked else 1.0

    @property
    def passed(self) -> bool:
        return not self.invented_identifiers and not self.ungrounded_numbers


def check(html_fragments: list[str], page_texts: dict[int, str]) -> VerbatimReport:
    report = VerbatimReport()
    source_text = " ".join(page_texts.values())
    source = _canon(source_text)
    if not source:
        return report

    source_numbers = {_canon_number(m.group(0)) for m in NUMBER.finditer(source_text)}
    text = " ".join(table_text(h) for h in html_fragments)

    for identifier in sorted(extract_identifiers(text)):
        report.checked += 1
        if _canon(identifier) in source:
            report.grounded += 1
        else:
            report.invented_identifiers.append(identifier)

    for match in NUMBER.finditer(text):
        raw = match.group(0)
        if len(_canon_number(raw)) < 3:  # line numbers and single digits are noise
            continue
        report.checked += 1
        if _canon_number(raw) in source_numbers:
            report.grounded += 1
        else:
            report.ungrounded_numbers.append(raw)

    return report
