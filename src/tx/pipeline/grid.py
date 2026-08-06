"""nested table parsing and grid representation
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup

#: Alphanumeric codes: the class of value a generative model silently rewrites.
IDENTIFIER = re.compile(r"\b(?=[A-Z0-9-]{5,})(?=[^\s]*[A-Z])(?=[^\s]*\d)[A-Z0-9][A-Z0-9-]{4,}\b")
_NUMERIC = re.compile(r"^[\s$€£]*[-+]?[\d.,\s]+%?$")
_DATE = re.compile(r"\b\d{1,4}[-/]\d{1,2}[-/]\d{1,4}\b")
#: '(A)', 'B.', 'iii)', '3' - the label column of an enumerated list.
_ENUMERATOR = re.compile(r"^[(\[]?\s*([A-Za-z]{1,3}|\d{1,3})\s*[)\].:]?$")


def _ordinal(text: str) -> int | None:
    match = _ENUMERATOR.match(norm_text(text))
    if not match:
        return None
    token = match.group(1)
    if token.isdigit():
        return int(token)
    return ord(token.lower()) if len(token) == 1 else None


def _leads_an_enumeration(rows: list[list[str]]) -> bool:
    """First column counts up from row 0, so row 0 is the first item and not a header."""
    if len(rows) < 3:
        return False
    ordinals = [_ordinal(row[0]) if row else None for row in rows]
    run = []
    for value in ordinals:
        if value is None:
            break
        run.append(value)
    return len(run) >= 3 and all(b - a == 1 for a, b in zip(run, run[1:], strict=False))


def norm_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").replace("\xa0", " ")).strip()


@dataclass
class Grid:
    rows: list[list[str]] = field(default_factory=list)
    n_header_rows: int = 1
    has_explicit_header: bool = False
    #: (row, col) -> inner table HTML, for cells that contained a table.
    nested: dict[tuple[int, int], str] = field(default_factory=dict)
    #: Leading full-width row, if any: a table caption rather than a column label.
    title: str | None = None

    @property
    def n_cols(self) -> int:
        return max((len(r) for r in self.rows), default=0)

    def is_banner(self, index: int) -> bool:
        """A row carrying one label across the table: a caption or section marker.

        Populated cells only, because a spanning cell that starts in column 2 leaves the
        earlier ones empty. The label must not be a number, or a row repeating one rate across
        every shift column would qualify.
        """
        if not 0 <= index < len(self.rows) or self.n_cols < 2:
            return False
        filled = [c for c in (norm_text(v) for v in self.rows[index]) if c]
        if len(filled) < 2 or len(filled) * 2 < self.n_cols:
            return False
        return len(set(filled)) == 1 and not _NUMERIC.match(filled[0])

    def header(self) -> list[str]:
        """One label per column, joining multi-level headers with ' / '."""
        if not self.rows:
            return []
        # No header means no labels: positional keys beat naming a column after a data row.
        if self.n_header_rows == 0:
            return [""] * self.n_cols
        # Banners repeat one string across every column, so they cannot distinguish columns.
        levels = [
            self.rows[i] for i in range(max(1, self.n_header_rows)) if not self.is_banner(i)
        ] or self.rows[:1]
        out: list[str] = []
        for col in range(self.n_cols):
            parts: list[str] = []
            for level in levels:
                cell = norm_text(level[col]) if col < len(level) else ""
                if cell and cell not in parts:
                    parts.append(cell)
            out.append(" / ".join(parts))
        return out

    def body(self) -> list[list[str]]:
        return self.rows[self.n_header_rows :]

    def column_signature(self) -> list[str]:
        """Per-column datatype profile, used to reject implausible merges."""
        sig: list[str] = []
        for col in range(self.n_cols):
            values = [norm_text(r[col]) for r in self.body() if col < len(r) and norm_text(r[col])]
            if not values:
                sig.append("empty")
            elif sum(bool(_DATE.search(v)) for v in values) > len(values) / 2:
                sig.append("date")
            elif sum(bool(_NUMERIC.match(v)) for v in values) > len(values) / 2:
                sig.append("numeric")
            else:
                sig.append("text")
        return sig


def _cell_text(tag) -> tuple[str, str | None]:
    """Cell text with any nested table removed, plus that nested table's HTML."""
    inner = tag.find("table")
    nested_html = None
    if inner is not None:
        nested_html = str(inner)
        inner.extract()
    return norm_text(tag.get_text(" ", strip=True)), nested_html


def parse_table(html: str) -> Grid:
    soup = BeautifulSoup(html or "", "lxml")
    table = soup.find("table")
    if table is None:
        return Grid(rows=[], n_header_rows=0)

    #: Only rows belonging to this table, not to a table nested inside one of its cells.
    tr_tags = [tr for tr in table.find_all("tr") if tr.find_parent("table") is table]

    occupied: dict[tuple[int, int], str] = {}
    nested: dict[tuple[int, int], str] = {}
    explicit_header_rows: set[int] = set()

    for r, tr in enumerate(tr_tags):
        cells = [c for c in tr.find_all(["td", "th"]) if c.find_parent("tr") is tr]
        if cells and all(c.name == "th" for c in cells):
            explicit_header_rows.add(r)
        col = 0
        for cell in cells:
            while (r, col) in occupied:
                col += 1
            text, inner = _cell_text(cell)
            try:
                colspan = max(1, int(cell.get("colspan", 1)))
                rowspan = max(1, int(cell.get("rowspan", 1)))
            except (TypeError, ValueError):
                colspan = rowspan = 1
            if inner:
                nested[(r, col)] = inner
            for dr in range(rowspan):
                for dc in range(colspan):
                    occupied[(r + dr, col + dc)] = text
            col += colspan

    if not occupied:
        return Grid(rows=[], n_header_rows=0)

    n_rows = max(r for r, _ in occupied) + 1
    n_cols = max(c for _, c in occupied) + 1
    rows = [[occupied.get((r, c), "") for c in range(n_cols)] for r in range(n_rows)]

    has_explicit = bool(explicit_header_rows) or table.find("thead") is not None
    if explicit_header_rows:
        n_header = 0
        while n_header in explicit_header_rows:
            n_header += 1
    elif table.find("thead") is not None:
        n_header = len(
            [tr for tr in table.find("thead").find_all("tr") if tr.find_parent("table") is table]
        )
    else:
        # Losing a row is invisible and unrecoverable; a spurious header row is neither.
        n_header = 0 if _leads_an_enumeration(rows) else (1 if n_rows > 1 else 0)

    grid = Grid(
        rows=rows,
        n_header_rows=min(n_header, n_rows),
        has_explicit_header=has_explicit,
        nested=nested,
    )

    # A section banner ('CSR - Crane, Scaffold & Rigging') is often marked up as <th>. Left in
    # the header block it renames every column and its row is lost, so return it to the body.
    while grid.n_header_rows > 1 and grid.is_banner(grid.n_header_rows - 1):
        grid.n_header_rows -= 1
    if grid.n_header_rows and grid.is_banner(0):
        grid.title = next((norm_text(c) for c in rows[0] if norm_text(c)), None)
    return grid


def grid_to_html(grid: Grid) -> str:
    parts = ["<table>"]
    if grid.n_header_rows:
        parts.append("<thead>")
        for row in grid.rows[: grid.n_header_rows]:
            parts.append("<tr>" + "".join(f"<th>{_escape(c)}</th>" for c in row) + "</tr>")
        parts.append("</thead>")
    parts.append("<tbody>")
    for row in grid.body():
        parts.append("<tr>" + "".join(f"<td>{_escape(c)}</td>" for c in row) + "</tr>")
    parts.append("</tbody></table>")
    return "".join(parts)


def _escape(value: str) -> str:
    return (value or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def top_level_tables(html: str) -> list[str]:
    """Split a document of HTML into top-level tables, ignoring nested ones."""
    out: list[str] = []
    depth = 0
    start: int | None = None
    for match in re.finditer(r"<\s*(/?)table[^>]*>", html or "", re.I):
        if match.group(1) != "/":
            if depth == 0:
                start = match.start()
            depth += 1
        else:
            depth -= 1
            if depth == 0 and start is not None:
                out.append(html[start : match.end()])
                start = None
    return out


def table_text(html: str) -> str:
    return norm_text(BeautifulSoup(html or "", "lxml").get_text(" ", strip=True))


def extract_identifiers(text: str) -> set[str]:
    return set(IDENTIFIER.findall(text or ""))


def has_nested_table(html: str) -> bool:
    soup = BeautifulSoup(html or "", "lxml")
    table = soup.find("table")
    return bool(table and table.find("table"))
