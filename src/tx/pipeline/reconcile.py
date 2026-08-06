"""Reconcile generated text against what the OCR engine actually read.

A vision model's identifier errors are near misses, not inventions from nothing: ALRD00882
becomes ALU000892, seven of nine characters correct, and the true string is sitting in the OCR
output of the same page. So identifiers are checked against that vocabulary and repaired when
the match is close and unambiguous, blanked when it is not.

Numbers are treated differently, and deliberately. Blanking any number the OCR leg did not see
emptied 107 correct price cells on the reference contract, because on a rasterised page the OCR
leg is the *weaker* reader. Detection is kept, deletion is off by default: an ungrounded number
is reported as a warning and left intact.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup
from rapidfuzz import process
from rapidfuzz.distance import Levenshtein

from tx.core.config import settings
from tx.pipeline.grid import IDENTIFIER

#: No space inside the class: it would swallow the gap between two adjacent cells, so OCR
#: output like "5 838.13" becomes one token and the real value never enters the vocabulary.
NUMBER = re.compile(r"[-+]?\d[\d,.]*\d|\d")


@dataclass
class ReconcileReport:
    kept: int = 0
    repaired: int = 0
    blanked: int = 0
    numbers_normalised: int = 0
    numbers_ungrounded: int = 0
    repairs: list[tuple[str, str]] = field(default_factory=list)
    blanks: list[str] = field(default_factory=list)
    ungrounded: list[str] = field(default_factory=list)
    #: Value as it now stands -> how confident we are in it, for the per-cell confidence column.
    confidence: dict[str, float] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return self.kept + self.repaired + self.blanked

    @property
    def grounded_rate(self) -> float:
        return (self.kept + self.repaired) / self.total if self.total else 1.0

    def merge(self, other: ReconcileReport) -> None:
        self.kept += other.kept
        self.repaired += other.repaired
        self.blanked += other.blanked
        self.numbers_normalised += other.numbers_normalised
        self.numbers_ungrounded += other.numbers_ungrounded
        self.repairs.extend(other.repairs)
        self.blanks.extend(other.blanks)
        self.ungrounded.extend(other.ungrounded)
        self.confidence.update(other.confidence)


def _digits(value: str) -> str:
    return re.sub(r"\D", "", value)


def build_vocabulary(page_texts: dict[int, str]) -> tuple[set[str], dict[str, str], set[str]]:
    """Identifiers, a digit-signature to canonical-form map, and every signature seen."""
    text = " ".join(page_texts.values())
    identifiers = set(IDENTIFIER.findall(text))

    canonical: dict[str, str] = {}
    ambiguous: set[str] = set()
    seen: set[str] = set()
    for match in NUMBER.finditer(text):
        raw = match.group(0).strip()
        signature = _digits(raw)
        if len(signature) < 3:
            continue
        seen.add(signature)
        if signature in canonical and canonical[signature] != raw:
            ambiguous.add(signature)
        canonical.setdefault(signature, raw)
    for signature in ambiguous:
        canonical.pop(signature, None)
    return identifiers, canonical, seen


def _nearest(token: str, vocabulary: set[str]) -> tuple[str | None, float]:
    if not vocabulary:
        return None, 0.0
    hits = process.extract(token, vocabulary, scorer=Levenshtein.normalized_similarity, limit=2)
    if not hits:
        return None, 0.0
    best, score = hits[0][0], hits[0][1]
    if score < settings.identifier_min_similarity:
        return None, score
    # Two plausible candidates means we cannot tell which was intended.
    if len(hits) > 1 and score - hits[1][1] < settings.identifier_margin:
        return None, score
    return best, score


def reconcile_html(html: str, page_texts: dict[int, str]) -> tuple[str, ReconcileReport]:
    report = ReconcileReport()
    vocabulary, canonical, seen = build_vocabulary(page_texts)
    if not vocabulary and not canonical:
        return html, report

    soup = BeautifulSoup(html, "lxml")
    for cell in soup.find_all(["td", "th"]):
        # Strings owned directly by this cell; a nested table's cells are visited separately,
        # so nesting cannot shield a bad identifier from repair.
        owned = [s for s in cell.find_all(string=True) if s.find_parent(["td", "th"]) is cell]
        for node in owned:
            text = str(node)
            changed = False

            if settings.ground_identifiers:
                for token in sorted(set(IDENTIFIER.findall(text)), key=len, reverse=True):
                    if token in vocabulary:
                        report.kept += 1
                        report.confidence[token] = 1.0
                        continue
                    fixed, score = _nearest(token, vocabulary)
                    if fixed:
                        report.repaired += 1
                        report.repairs.append((token, fixed))
                        report.confidence[fixed] = round(score, 3)
                        text = text.replace(token, fixed)
                    else:
                        report.blanked += 1
                        report.blanks.append(token)
                        # Fail closed on identifiers: an empty cell is reviewed, a plausible
                        # wrong part number is ordered.
                        text = text.replace(token, "")
                    changed = True

            def fix_number(match: re.Match) -> str:
                raw = match.group(0)
                signature = _digits(raw)
                if (form := canonical.get(signature)) and form != raw.strip():
                    report.numbers_normalised += 1
                    return form
                if len(signature) >= 3 and signature not in seen:
                    report.numbers_ungrounded += 1
                    report.ungrounded.append(raw.strip())
                    if settings.ground_numbers:
                        return ""
                return raw

            updated = NUMBER.sub(fix_number, text)
            if changed or updated != str(node):
                node.replace_with(updated)

    body = soup.find("body")
    out = "".join(str(c) for c in body.contents) if body else str(soup)
    return out, report
