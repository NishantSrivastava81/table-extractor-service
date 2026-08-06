"""An extractor that cannot fail, for testing everything that is not extraction.

It calls no provider and returns a fixed, valid result. When the service misbehaves while this
is selected, the fault is in the API, the store, the queue or the worker, never in the
extraction. That separation is worth far more than the hour it costs to write.
"""

from __future__ import annotations

import time

from tx import __version__
from tx.core.models import (
    Column,
    ExtractionInfo,
    Job,
    Progress,
    Result,
    Summary,
    Table,
    utcnow,
)
from tx.pipeline.base import ProgressFn

_COLUMNS = [
    Column(index=0, key="item", label="Item", type="string"),
    Column(index=1, key="description", label="Description", type="string"),
    Column(index=2, key="amount", label="Amount", type="money", currency="AUD"),
]

_ROWS = [
    ("010", "Aluminium Round Bar", "93.00"),
    ("020", "Stainless Steel Sheet", "124.94"),
    ("030", "Insulation Material", "26.25"),
]


class NullExtractor:
    """Deterministic output, so golden tests can assert on it byte for byte."""

    name = "null"

    def __init__(self, delay_seconds: float = 0.0, pages: int = 1) -> None:
        self._delay = delay_seconds
        self._pages = pages

    def extract(self, pdf: bytes, job: Job, on_progress: ProgressFn) -> Result:
        started = utcnow()
        pages = job.document.pages or self._pages

        on_progress(Progress(stage="ocr", pages_total=pages))
        if self._delay:
            time.sleep(self._delay)
        on_progress(Progress(stage="vision", pages_total=pages, pages_routed=1, pages_done=1))

        table = Table(
            table_id="t_0001",
            pages=[1],
            title="Null extractor sample",
            n_rows=len(_ROWS),
            n_cols=len(_COLUMNS),
            columns=_COLUMNS,
            header_rows=[[c.label for c in _COLUMNS]],
            rows=[
                {
                    "row_index": i,
                    "item": {"raw": item, "value": item, "confidence_source": "unverified"},
                    "description": {
                        "raw": desc,
                        "value": desc,
                        "confidence_source": "unverified",
                    },
                    "amount": {
                        "raw": f"$ {amount}",
                        "value": float(amount),
                        "currency": "AUD",
                        "confidence_source": "unverified",
                    },
                }
                for i, (item, desc, amount) in enumerate(_ROWS)
            ],
            provenance={"source": "null"},
        )

        completed = utcnow()
        on_progress(
            Progress(
                stage="done",
                pages_total=pages,
                pages_routed=1,
                pages_done=1,
                tables_found=1,
            )
        )
        return Result(
            job_id=job.job_id,
            document=job.document,
            extraction=ExtractionInfo(
                engine_version=f"null-{__version__}",
                mode=job.options.mode,
                started_at=started,
                completed_at=completed,
                duration_s=(completed - started).total_seconds(),
                pages_routed_to_vision=0,
            ),
            summary=Summary(
                tables=1,
                rows=len(_ROWS),
                cells_populated=len(_ROWS) * len(_COLUMNS),
                verbatim_score=1.0,
            ),
            tables=[table],
        )
