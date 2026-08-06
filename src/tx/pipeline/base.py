"""What the worker needs from an extractor, and nothing more.

Keeping this narrow is what lets the whole service be tested without spending a penny: the null
implementation satisfies it completely.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from tx.core.models import Job, Progress, Result

ProgressFn = Callable[[Progress], None]


class Extractor(Protocol):
    def extract(self, pdf: bytes, job: Job, on_progress: ProgressFn) -> Result: ...
