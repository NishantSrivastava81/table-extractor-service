"""Request and response bodies for the job API."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from tx.core.models import DocumentInfo, Job, JobError, JobStatus


class JobLinks(BaseModel):
    self: str
    result: str


class JobResponse(BaseModel):
    job_id: str
    status: JobStatus
    mode: str
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    document: DocumentInfo
    progress: dict
    error: JobError | None = None
    links: JobLinks

    @classmethod
    def of(cls, job: Job) -> JobResponse:
        return cls(
            job_id=job.job_id,
            status=job.status,
            mode=job.options.mode,
            created_at=job.created_at,
            started_at=job.started_at,
            completed_at=job.completed_at,
            document=job.document,
            progress={**job.progress.model_dump(), "percent": job.progress.percent},
            error=job.error,
            links=JobLinks(
                self=f"/v1/jobs/{job.job_id}",
                result=f"/v1/jobs/{job.job_id}/result",
            ),
        )


class JobListResponse(BaseModel):
    jobs: list[JobResponse] = Field(default_factory=list)
    next_cursor: str | None = None
