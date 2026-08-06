"""Grid to flat, typed rows.

Nested tables are exploded into the parent rather than dropped: the parent row repeats once per
child row and the child columns are appended, prefixed with the parent column key. Two
bookkeeping columns, `_parent_row_index` and `_nest_path`, make the explosion reversible. The
values and their parent association survive; only the original shape is gone.
"""

from __future__ import annotations

import re
from collections import Counter

from tx.core.config import settings
from tx.core.models import Cell, Column, Table, Warning_
from tx.pipeline.grid import Grid, norm_text, parse_table
from tx.pipeline.locale import (
    NumberFormat,
    detect_currency,
    infer_format,
    is_numeric_literal,
    looks_like_money,
    looks_like_percent,
    parse_number,
    repair_separators,
)

PARENT_ROW_KEY = "_parent_row_index"
NEST_PATH_KEY = "_nest_path"
_NON_KEY = re.compile(r"[^a-z0-9]+")
_DATE = re.compile(r"^\s*\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}\s*$")
_HAS_DIGIT = re.compile(r"\d")
_BOOL = {"yes", "no", "true", "false", "y", "n"}


def derive_keys(labels: list[str]) -> list[str]:
    """Stable, bindable column keys. Duplicates get a numeric suffix rather than colliding."""
    keys: list[str] = []
    seen: Counter[str] = Counter()
    for index, label in enumerate(labels):
        base = _NON_KEY.sub("_", norm_text(label).lower()).strip("_")
        if not base or base.isdigit():
            base = f"col_{index}"
        seen[base] += 1
        keys.append(base if seen[base] == 1 else f"{base}_{seen[base]}")
    return keys


def _flatten_grid(grid: Grid, depth: int) -> tuple[list[str], list[dict[str, str]]]:
    """(ordered column keys, rows as key -> raw text) with nested tables exploded."""
    keys = derive_keys(grid.header())
    columns: list[str] = list(keys)
    rows: list[dict[str, str]] = []

    for offset, row in enumerate(grid.body()):
        absolute = offset + grid.n_header_rows
        base = {keys[c]: row[c] for c in range(min(len(keys), len(row)))}

        # A section row spans the table, so parsing repeats its label in every column. Keep it
        # once: left in place it lands a string in each money column and warns on all of them.
        if grid.is_banner(absolute):
            label = next((v for v in row if norm_text(v)), "")
            base = {k: (label if i == 0 else "") for i, k in enumerate(keys)}
            rows.append(base)
            continue

        nested = [
            (c, grid.nested[(absolute, c)])
            for c in range(grid.n_cols)
            if (absolute, c) in grid.nested
        ]
        if not nested or depth <= 0:
            rows.append(base)
            continue

        expanded: list[tuple[str, list[str], list[dict[str, str]]]] = []
        for column_index, html in nested:
            child = parse_table(html)
            child_keys, child_rows = _flatten_grid(child, depth - 1)
            prefix = keys[column_index] if column_index < len(keys) else f"col_{column_index}"
            renamed_keys = [f"{prefix}__{k}" for k in child_keys]
            renamed_rows = [
                {f"{prefix}__{k}": v for k, v in child_row.items()} for child_row in child_rows
            ]
            expanded.append((prefix, renamed_keys, renamed_rows))
            for key in renamed_keys:
                if key not in columns:
                    columns.append(key)

        # Several nested tables in one row are paired by index, never crossed: a cross product
        # would multiply rows without any evidence the combinations are real.
        height = max((len(child_rows) for _, _, child_rows in expanded), default=0)
        if height == 0:
            rows.append(base)
            continue

        for i in range(height):
            merged = dict(base)
            paths: list[str] = []
            for prefix, _, child_rows in expanded:
                if i < len(child_rows):
                    merged.update(child_rows[i])
                    paths.append(f"{prefix}[{i}]")
            merged[PARENT_ROW_KEY] = str(offset)
            merged[NEST_PATH_KEY] = ",".join(paths)
            rows.append(merged)

    if any(PARENT_ROW_KEY in r for r in rows):
        for key in (PARENT_ROW_KEY, NEST_PATH_KEY):
            if key not in columns:
                columns.append(key)
    return columns, rows


def _infer_type(values: list[str]) -> str:
    populated = [v for v in values if norm_text(v)]
    if not populated:
        return "empty"
    if sum(looks_like_percent(v) for v in populated) > len(populated) / 2:
        return "percent"
    if sum(looks_like_money(v) for v in populated) > len(populated) / 2:
        return "money"
    if sum(bool(_DATE.match(v)) for v in populated) > len(populated) / 2:
        return "date"
    if sum(norm_text(v).lower() in _BOOL for v in populated) > len(populated) / 2:
        return "boolean"
    if sum(is_numeric_literal(v) for v in populated) > len(populated) / 2:
        return "number"
    return "string"


def flatten(
    table_id: str,
    html: str,
    pages: list[int],
    *,
    title: str | None = None,
    locale_hint: str | None = None,
    default_currency: str | None = None,
    confidence: dict[str, float] | None = None,
    provenance: dict | None = None,
) -> Table:
    grid = parse_table(html)
    if not grid.rows:
        return Table(table_id=table_id, pages=pages, title=title, provenance=provenance or {})

    title = title or grid.title
    keys, raw_rows = _flatten_grid(grid, settings.max_nest_depth)
    labels = dict(zip(derive_keys(grid.header()), grid.header(), strict=False))
    confidence = confidence or {}
    warnings: list[Warning_] = []

    columns: list[Column] = []
    formats: dict[str, NumberFormat | None] = {}
    for index, key in enumerate(keys):
        values = [row.get(key, "") for row in raw_rows]
        if key in (PARENT_ROW_KEY, NEST_PATH_KEY):
            columns.append(Column(index=index, key=key, label=key, type="string"))
            formats[key] = None
            continue

        kind = _infer_type(values)
        currency = None
        if kind in {"money", "number", "percent"}:
            fmt, ambiguous = infer_format(values, locale_hint)
            formats[key] = fmt
            if ambiguous:
                warnings.append(
                    Warning_(
                        code="LOCALE_AMBIGUOUS",
                        column=key,
                        detail=(
                            "Thousands and decimal separators cannot be resolved for this column "
                            "from its own values. Raw text is preserved and value is null."
                        ),
                    )
                )
            if kind == "money":
                currency = detect_currency(
                    next((v for v in values if looks_like_money(v)), ""), default_currency
                )
        else:
            formats[key] = None

        columns.append(
            Column(
                index=index,
                key=key,
                label=labels.get(key, key.replace("_", " ").title()),
                type=kind,
                currency=currency,
            )
        )

    by_key = {c.key: c for c in columns}
    rows: list[dict] = []
    populated = 0
    for row_index, raw_row in enumerate(raw_rows):
        record: dict = {"row_index": row_index}
        for key in keys:
            text = raw_row.get(key, "")
            if norm_text(text):
                populated += 1
            column = by_key[key]
            value = None
            if column.type in {"money", "number", "percent"}:
                parsed = parse_number(text, formats.get(key))
                value = float(parsed) if parsed is not None else None
                if value is None and (repaired := repair_separators(text)) is not None:
                    value = float(repaired)
                    warnings.append(
                        Warning_(
                            code="SEPARATOR_REPAIRED",
                            row_index=row_index,
                            column=key,
                            value=text,
                            detail=f"Separators did not fit the column; read as {value}.",
                        )
                    )
                # '-' or 'N/A' in a rate cell means there is no rate, not a broken number. Only
                # a cell that tried to be a number and failed is worth a warning.
                elif value is None and _HAS_DIGIT.search(text or ""):
                    warnings.append(
                        Warning_(
                            code="VALUE_NOT_PARSED",
                            row_index=row_index,
                            column=key,
                            value=text,
                            detail="Raw text kept; it did not match the column's number format.",
                        )
                    )
            elif column.type == "boolean":
                lowered = norm_text(text).lower()
                value = lowered in {"yes", "true", "y"} if lowered in _BOOL else None
            else:
                value = norm_text(text) or None

            score = confidence.get(norm_text(text))
            record[key] = Cell(
                raw=text,
                value=value,
                currency=column.currency if column.type == "money" else None,
                confidence=score,
                confidence_source="grounding" if score is not None else "unverified",
            ).model_dump()
        rows.append(record)

    exploded = any(PARENT_ROW_KEY in r for r in raw_rows)
    return Table(
        table_id=table_id,
        pages=pages,
        title=title,
        n_rows=len(rows),
        n_cols=len(columns),
        rows_before_explosion=len(grid.body()) if exploded else None,
        columns=columns,
        header_rows=[list(r) for r in grid.rows[: grid.n_header_rows]],
        rows=rows,
        provenance={**(provenance or {}), "cells_populated": populated},
        warnings=warnings[:50],
    )
