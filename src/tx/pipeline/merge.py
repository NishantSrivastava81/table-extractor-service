"""Cross-page merge.

Document Intelligence does not merge a table that spans pages, and neither does a per-page
vision call, so fragments arrive separately and something has to join them.

The decision and the execution are kept apart. A deterministic filter rejects pairs that cannot
be continuations, a model answers only the narrow question "are these one table?", and a
deterministic stitch does the joining.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from rapidfuzz.distance import Levenshtein

from tx.core.config import settings
from tx.core.logging import get_logger
from tx.pipeline.grid import Grid, grid_to_html, norm_text, parse_table
from tx.pipeline.llm import Usage, chat

log = get_logger(__name__)


@dataclass
class Fragment:
    fragment_id: str
    html: str
    page: int
    title: str | None = None


@dataclass
class MergedTable:
    html: str
    pages: list[int]
    title: str | None = None
    merged_from: list[str] = field(default_factory=list)


SYSTEM = """You decide whether two table fragments extracted from a PDF are parts of ONE
logical table.

You will see the LAST rows of fragment A and the FIRST rows of fragment B.

Answer merge=true only if B continues the same record sequence as A.
Evidence for merging:
- identical or near-identical column headers repeated at the start of B
- explicit continuation cues such as "(continued)", "carried forward", "cont'd"
- the column count and per-column data types match, and B's first row is a plausible next
  record rather than a new subject

Evidence against merging:
- B has a different subject, caption or heading introducing a new table
- column counts or data types disagree
- A ends with a totals or summary row that closes the table

Respond with JSON only: {"merge": true|false, "confidence": 0.0-1.0, "reason": "<= 20 words"}"""


def _header_similarity(a: Grid, b: Grid) -> float:
    ha, hb = a.header(), b.header()
    if not ha or not hb or len(ha) != len(hb):
        return 0.0
    scores = []
    for x, y in zip(ha, hb, strict=True):
        x, y = norm_text(x), norm_text(y)
        if not x and not y:
            scores.append(1.0)
        elif not x or not y:
            scores.append(0.0)
        else:
            scores.append(1.0 - Levenshtein.distance(x, y) / max(len(x), len(y)))
    return sum(scores) / len(scores)


def _header_conflict(a: Grid, b: Grid) -> bool:
    """Both fragments declare a header and the two headers disagree.

    Only declared headers count. A continuation fragment frequently carries none, and the parser
    defaults to treating its first data row as one, so testing similarity without this guard
    would veto exactly the merges this stage exists to perform.
    """
    if not a.has_explicit_header or not b.has_explicit_header:
        return False
    if not a.header() or not b.header():
        return False
    return _header_similarity(a, b) < settings.merge_header_conflict_threshold


def _caption_conflict(a: Grid, b: Grid) -> bool:
    """Two captions that disagree name two different tables, however alike their columns look."""
    x, y = norm_text(a.title or ""), norm_text(b.title or "")
    if not x or not y or x == y:
        return False
    similarity = 1.0 - Levenshtein.distance(x, y) / max(len(x), len(y))
    return similarity < settings.merge_header_conflict_threshold


def _signature_match(a: Grid, b: Grid) -> bool:
    if a.n_cols != b.n_cols or a.n_cols == 0:
        return False
    sa, sb = a.column_signature(), b.column_signature()
    agree = sum(1 for x, y in zip(sa, sb, strict=True) if x == y or "empty" in (x, y))
    return agree / len(sa) >= settings.merge_signature_agreement


def _rows_equal(a: list[str], b: list[str], tolerance: float = 0.9) -> bool:
    if len(a) != len(b):
        return False
    scores = []
    for x, y in zip(a, b, strict=True):
        x, y = norm_text(x), norm_text(y)
        if not x and not y:
            scores.append(1.0)
        elif not x or not y:
            scores.append(0.0)
        else:
            scores.append(1.0 - Levenshtein.distance(x, y) / max(len(x), len(y)))
    return sum(scores) / len(scores) >= tolerance


def _looks_like_wrapped_tail(row: list[str], n_cols: int) -> bool:
    """A cell whose content broke across the page: one populated column, and not the first."""
    filled = [i for i, v in enumerate(row) if norm_text(v)]
    return len(filled) == 1 and filled[0] != 0 and n_cols > 2


def stitch(head_html: str, tail_html: str) -> str:
    head, tail = parse_table(head_html), parse_table(tail_html)
    if not head.rows:
        return tail_html
    if not tail.rows:
        return head_html

    width = max(head.n_cols, tail.n_cols)

    def pad(rows: list[list[str]]) -> list[list[str]]:
        return [r + [""] * (width - len(r)) for r in rows]

    merged = pad(list(head.rows))
    head_header = head.rows[: head.n_header_rows]
    body = tail.rows[tail.n_header_rows :] if tail.n_header_rows else list(tail.rows)
    if head_header and tail.n_header_rows == 0:
        # Some parsers do not mark a repeated header as one. Detect it by value.
        while body and any(_rows_equal(body[0], h) for h in head_header):
            body = body[1:]
    body = pad(body)

    if body and merged and _looks_like_wrapped_tail(body[0], width):
        col = next(i for i, v in enumerate(body[0]) if norm_text(v))
        merged[-1][col] = norm_text(merged[-1][col] + " " + body[0][col])
        body = body[1:]

    merged.extend(body)
    return grid_to_html(Grid(rows=merged, n_header_rows=head.n_header_rows or 1))


def _decide(a: Fragment, b: Fragment, usage: Usage | None) -> dict:
    ga, gb = parse_table(a.html), parse_table(b.html)
    heuristic = {
        "merge": _header_similarity(ga, gb) >= 0.85,
        "confidence": 0.6,
        "reason": "heuristic: header repeated",
        "decided_by": "heuristic",
    }
    if not settings.merge_llm_configured:
        return heuristic

    def preview(grid: Grid, head: bool, n: int = 3) -> str:
        rows = grid.body()[:n] if head else grid.body()[-n:]
        lines = (grid.rows[: grid.n_header_rows] + rows) if head else rows
        return "\n".join(" | ".join(r) for r in lines) or "(empty)"

    user = (
        f"FRAGMENT A (page {a.page}, last rows):\n{preview(ga, head=False)}\n\n"
        f"FRAGMENT B (page {b.page}, header + first rows):\n{preview(gb, head=True)}"
    )
    try:
        raw = chat(
            settings.foundry_merge_deployment,
            [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
            max_tokens=settings.merge_max_tokens,
            usage=usage,
            response_format={"type": "json_object"},
        )
        match = re.search(r"\{.*\}", raw, re.S)
        data = json.loads(match.group(0) if match else raw)
        return {
            "merge": bool(data.get("merge")),
            "confidence": float(data.get("confidence", 0.5)),
            "reason": str(data.get("reason", ""))[:160],
            "decided_by": settings.foundry_merge_deployment,
        }
    except Exception as exc:  # noqa: BLE001 - fall back rather than fail the document
        heuristic["reason"] = f"llm unavailable ({type(exc).__name__}), {heuristic['reason']}"
        return heuristic


def merge_fragments(
    fragments: list[Fragment], usage: Usage | None = None
) -> tuple[list[MergedTable], list[dict]]:
    if len(fragments) < 2:
        return (
            [MergedTable(f.html, [f.page], f.title, [f.fragment_id]) for f in fragments],
            [],
        )

    ordered = sorted(fragments, key=lambda f: (f.page, f.fragment_id))
    parent: dict[str, str] = {}
    log_entries: list[dict] = []

    def root(fid: str) -> str:
        while parent.get(fid, fid) != fid:
            fid = parent[fid]
        return fid

    # Only the last fragment on a page can continue onto the first fragment of the next.
    by_page: dict[int, list[Fragment]] = {}
    for fragment in ordered:
        by_page.setdefault(fragment.page, []).append(fragment)

    pages = sorted(by_page)
    for index, page in enumerate(pages[:-1]):
        following = by_page[pages[index + 1]]
        if pages[index + 1] - page > 1 or not following:
            continue
        a, b = by_page[page][-1], following[0]
        ga, gb = parse_table(a.html), parse_table(b.html)

        if _header_conflict(ga, gb) or _caption_conflict(ga, gb) or not _signature_match(ga, gb):
            log_entries.append(
                {"a": a.fragment_id, "b": b.fragment_id, "merge": False, "reason": "filtered"}
            )
            continue

        verdict = _decide(a, b, usage)
        log_entries.append({"a": a.fragment_id, "b": b.fragment_id, **verdict})
        if verdict["merge"]:
            parent[b.fragment_id] = root(a.fragment_id)

    groups: dict[str, list[Fragment]] = {}
    for fragment in ordered:
        groups.setdefault(root(fragment.fragment_id), []).append(fragment)

    out: list[MergedTable] = []
    for fragment in ordered:
        if root(fragment.fragment_id) != fragment.fragment_id:
            continue
        chain = groups[fragment.fragment_id]
        html = chain[0].html
        for nxt in chain[1:]:
            html = stitch(html, nxt.html)
        out.append(
            MergedTable(
                html=html,
                pages=sorted({f.page for f in chain}),
                title=chain[0].title,
                merged_from=[f.fragment_id for f in chain],
            )
        )
    return out, log_entries
