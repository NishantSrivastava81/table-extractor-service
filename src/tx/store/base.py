"""Job store contract.

Also the queue: a job *is* the queue entry, claimed by lease. One less moving part, and the
claim is transactional so two workers cannot take the same job.
"""

from __future__ import annotations

from typing import Protocol

from tx.core.models import Job, JobError, JobStatus, Progress


class JobStore(Protocol):
    def ping(self) -> None: ...

    def create(self, job: Job) -> Job: ...

    def get(self, job_id: str, principal_id: str | None = None) -> Job | None: ...

    def list(
        self,
        principal_id: str,
        status: JobStatus | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> list[Job]: ...

    def find_by_idempotency_key(self, principal_id: str, key: str) -> Job | None: ...

    def find_live_by_hash(
        self, principal_id: str, sha256: str, options_json: str | None = None
    ) -> Job | None:
        """Same bytes AND same options. Different options are a different question."""

    def claim_next(self, lease_seconds: int) -> Job | None:
        """Atomically move one queued job to running and take a lease on it."""

    def heartbeat(self, job_id: str, lease_seconds: int) -> None: ...

    def set_progress(self, job_id: str, progress: Progress) -> None: ...

    def set_pages(self, job_id: str, pages: int) -> None: ...

    def finish(self, job_id: str, status: JobStatus, error: JobError | None = None) -> None: ...

    def cancel(self, job_id: str, principal_id: str) -> bool: ...

    def delete(self, job_id: str, principal_id: str) -> bool: ...

    def reclaim_expired_leases(self, max_attempts: int) -> int:
        """Return jobs whose worker died to the queue. Returns how many were reclaimed."""
