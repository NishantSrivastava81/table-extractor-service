"""Domain models shared by the API, the store and the pipeline."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(UTC)


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


TERMINAL = {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.EXPIRED}

ExtractionMode = Literal["fast", "balanced", "thorough"]
FlattenStyle = Literal["objects", "rows"]


class Progress(BaseModel):
    stage: str = "queued"
    pages_total: int = 0
    pages_routed: int = 0
    pages_done: int = 0
    tables_found: int = 0

    @property
    def percent(self) -> int:
        if self.stage == "done":
            return 100
        if not self.pages_routed:
            return 5 if self.pages_total else 0
        return min(95, 10 + int(85 * self.pages_done / self.pages_routed))


class JobError(BaseModel):
    code: str
    message: str


class DocumentInfo(BaseModel):
    filename: str | None = None
    sha256: str
    bytes: int
    pages: int = 0


class JobOptions(BaseModel):
    mode: ExtractionMode = "balanced"
    flatten: FlattenStyle = "objects"
    pages: str | None = None
    locale_hint: str | None = None
    default_currency: str | None = None


class Job(BaseModel):
    job_id: str
    principal_id: str
    status: JobStatus = JobStatus.QUEUED
    options: JobOptions = Field(default_factory=JobOptions)
    document: DocumentInfo
    progress: Progress = Field(default_factory=Progress)
    error: JobError | None = None
    attempts: int = 0
    idempotency_key: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    lease_expires_at: datetime | None = None

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL


# ------------------------------------------------------------------ result shape


class Cell(BaseModel):
    raw: str
    value: Any = None
    currency: str | None = None
    confidence: float | None = None
    confidence_source: Literal["ocr", "grounding", "unverified"] = "unverified"


class Column(BaseModel):
    index: int
    key: str
    label: str
    type: str = "string"
    currency: str | None = None


class Warning_(BaseModel):
    code: str
    detail: str
    row_index: int | None = None
    column: str | None = None
    value: str | None = None


class Table(BaseModel):
    table_id: str
    pages: list[int] = Field(default_factory=list)
    title: str | None = None
    n_rows: int = 0
    n_cols: int = 0
    rows_before_explosion: int | None = None
    columns: list[Column] = Field(default_factory=list)
    header_rows: list[list[str]] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)
    warnings: list[Warning_] = Field(default_factory=list)


class Summary(BaseModel):
    tables: int = 0
    tables_suppressed_as_furniture: int = 0
    rows: int = 0
    cells_populated: int = 0
    verbatim_score: float = 1.0
    warnings: int = 0


class ExtractionInfo(BaseModel):
    engine_version: str
    mode: str
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_s: float = 0.0
    pages_routed_to_vision: int = 0
    llm_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    partial: bool = False
    failed_pages: list[int] = Field(default_factory=list)


class Result(BaseModel):
    job_id: str
    schema_version: str = "1.0"
    document: DocumentInfo
    extraction: ExtractionInfo
    summary: Summary = Field(default_factory=Summary)
    tables: list[Table] = Field(default_factory=list)
    suppressed_tables: list[Table] = Field(default_factory=list)
