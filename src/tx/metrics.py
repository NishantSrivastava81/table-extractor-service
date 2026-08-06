"""Prometheus metrics.

Two of these matter more than job success rate, because they catch silent failure: quality
degrading while requests still return 200, and the router losing its discrimination so cost
runs away.
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

JOBS_SUBMITTED = Counter("tx_jobs_submitted_total", "Jobs accepted", ["principal"])
JOBS_COMPLETED = Counter("tx_jobs_completed_total", "Jobs reaching a terminal state", ["status"])
JOB_DURATION = Histogram(
    "tx_job_duration_seconds",
    "Wall clock per job",
    ["mode"],
    buckets=(1, 5, 15, 30, 60, 120, 300, 600, 1800, 3600),
)
STAGE_DURATION = Histogram(
    "tx_stage_duration_seconds",
    "Wall clock per pipeline stage",
    ["stage"],
    buckets=(0.1, 0.5, 1, 5, 15, 60, 300, 900),
)
QUEUE_DEPTH = Gauge("tx_queue_depth", "Jobs waiting to be claimed")

#: Cost is about to run away when this climbs.
PAGES_ROUTED_RATIO = Histogram(
    "tx_pages_routed_ratio",
    "Share of pages sent to the vision model",
    buckets=(0.0, 0.05, 0.1, 0.2, 0.3, 0.5, 0.75, 1.0),
)
#: Quality is degrading when this falls, even though jobs still succeed.
VERBATIM_SCORE = Histogram(
    "tx_verbatim_score",
    "Share of extracted values found in the OCR reading",
    buckets=(0.9, 0.95, 0.98, 0.99, 0.995, 1.0),
)

LLM_CALLS = Counter("tx_llm_calls_total", "Model calls", ["provider", "outcome"])
LLM_TOKENS = Counter("tx_llm_tokens_total", "Model tokens", ["provider", "kind"])
PROVIDER_RETRIES = Counter("tx_provider_retries_total", "Retried calls", ["provider", "reason"])
