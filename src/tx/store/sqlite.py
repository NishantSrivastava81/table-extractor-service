"""SQLite job store and queue.

WAL mode plus a process-wide lock. The lock is not a scalability compromise at this size: every
call is a sub-millisecond indexed statement, and it removes a whole class of concurrency bug
while the service runs single-process. Swapping in Postgres means implementing the same
protocol, not changing callers.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from tx.core.models import (
    DocumentInfo,
    Job,
    JobError,
    JobOptions,
    JobStatus,
    Progress,
    utcnow,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id            TEXT PRIMARY KEY,
    principal_id      TEXT NOT NULL,
    status            TEXT NOT NULL,
    options           TEXT NOT NULL,
    document          TEXT NOT NULL,
    progress          TEXT NOT NULL,
    error             TEXT,
    attempts          INTEGER NOT NULL DEFAULT 0,
    idempotency_key   TEXT,
    created_at        TEXT NOT NULL,
    started_at        TEXT,
    completed_at      TEXT,
    lease_expires_at  TEXT
);
CREATE INDEX IF NOT EXISTS ix_jobs_queue     ON jobs(status, created_at);
CREATE INDEX IF NOT EXISTS ix_jobs_principal ON jobs(principal_id, created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS ux_jobs_idem ON jobs(principal_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;
"""


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


class SqliteJobStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        with self._lock:
            self._conn.executescript(_SCHEMA)

    # ------------------------------------------------------------------ helpers

    def _row_to_job(self, row: sqlite3.Row) -> Job:
        return Job(
            job_id=row["job_id"],
            principal_id=row["principal_id"],
            status=JobStatus(row["status"]),
            options=JobOptions.model_validate_json(row["options"]),
            document=DocumentInfo.model_validate_json(row["document"]),
            progress=Progress.model_validate_json(row["progress"]),
            error=JobError.model_validate_json(row["error"]) if row["error"] else None,
            attempts=row["attempts"],
            idempotency_key=row["idempotency_key"],
            created_at=_parse(row["created_at"]),
            started_at=_parse(row["started_at"]),
            completed_at=_parse(row["completed_at"]),
            lease_expires_at=_parse(row["lease_expires_at"]),
        )

    def _one(self, sql: str, params: tuple[Any, ...]) -> Job | None:
        with self._lock:
            row = self._conn.execute(sql, params).fetchone()
        return self._row_to_job(row) if row else None

    # ------------------------------------------------------------------ api

    def ping(self) -> None:
        with self._lock:
            self._conn.execute("SELECT 1").fetchone()

    def create(self, job: Job) -> Job:
        with self._lock:
            self._conn.execute(
                """INSERT INTO jobs (job_id, principal_id, status, options, document, progress,
                                     error, attempts, idempotency_key, created_at, started_at,
                                     completed_at, lease_expires_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    job.job_id,
                    job.principal_id,
                    job.status.value,
                    job.options.model_dump_json(),
                    job.document.model_dump_json(),
                    job.progress.model_dump_json(),
                    job.error.model_dump_json() if job.error else None,
                    job.attempts,
                    job.idempotency_key,
                    _iso(job.created_at),
                    _iso(job.started_at),
                    _iso(job.completed_at),
                    _iso(job.lease_expires_at),
                ),
            )
        return job

    def get(self, job_id: str, principal_id: str | None = None) -> Job | None:
        if principal_id is None:
            return self._one("SELECT * FROM jobs WHERE job_id = ?", (job_id,))
        return self._one(
            "SELECT * FROM jobs WHERE job_id = ? AND principal_id = ?", (job_id, principal_id)
        )

    def list(
        self,
        principal_id: str,
        status: JobStatus | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> list[Job]:
        sql = "SELECT * FROM jobs WHERE principal_id = ?"
        params: list[Any] = [principal_id]
        if status:
            sql += " AND status = ?"
            params.append(status.value)
        if cursor:
            sql += " AND job_id < ?"
            params.append(cursor)
        sql += " ORDER BY job_id DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, tuple(params)).fetchall()
        return [self._row_to_job(r) for r in rows]

    def find_by_idempotency_key(self, principal_id: str, key: str) -> Job | None:
        return self._one(
            "SELECT * FROM jobs WHERE principal_id = ? AND idempotency_key = ?",
            (principal_id, key),
        )

    def find_live_by_hash(
        self, principal_id: str, sha256: str, options_json: str | None = None
    ) -> Job | None:
        sql = """SELECT * FROM jobs
                 WHERE principal_id = ? AND status IN ('queued','running','succeeded')
                   AND json_extract(document, '$.sha256') = ?"""
        params: list[Any] = [principal_id, sha256]
        if options_json is not None:
            sql += " AND options = ?"
            params.append(options_json)
        sql += " ORDER BY created_at DESC LIMIT 1"
        with self._lock:
            row = self._conn.execute(sql, tuple(params)).fetchone()
        return self._row_to_job(row) if row else None

    def claim_next(self, lease_seconds: int) -> Job | None:
        now = utcnow()
        lease = now + timedelta(seconds=lease_seconds)
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._conn.execute(
                    "SELECT job_id FROM jobs WHERE status = 'queued' ORDER BY created_at LIMIT 1"
                ).fetchone()
                if row is None:
                    self._conn.execute("COMMIT")
                    return None
                self._conn.execute(
                    """UPDATE jobs
                       SET status='running', started_at=COALESCE(started_at, ?),
                           lease_expires_at=?, attempts=attempts+1
                       WHERE job_id=?""",
                    (_iso(now), _iso(lease), row["job_id"]),
                )
                claimed = self._conn.execute(
                    "SELECT * FROM jobs WHERE job_id = ?", (row["job_id"],)
                ).fetchone()
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
        return self._row_to_job(claimed)

    def heartbeat(self, job_id: str, lease_seconds: int) -> None:
        lease = utcnow() + timedelta(seconds=lease_seconds)
        with self._lock:
            self._conn.execute(
                "UPDATE jobs SET lease_expires_at = ? WHERE job_id = ? AND status = 'running'",
                (_iso(lease), job_id),
            )

    def set_progress(self, job_id: str, progress: Progress) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE jobs SET progress = ? WHERE job_id = ?",
                (progress.model_dump_json(), job_id),
            )

    def set_pages(self, job_id: str, pages: int) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE jobs SET document = json_set(document, '$.pages', ?) WHERE job_id = ?",
                (pages, job_id),
            )

    def finish(self, job_id: str, status: JobStatus, error: JobError | None = None) -> None:
        with self._lock:
            self._conn.execute(
                """UPDATE jobs SET status=?, error=?, completed_at=?, lease_expires_at=NULL
                   WHERE job_id=?""",
                (
                    status.value,
                    error.model_dump_json() if error else None,
                    _iso(utcnow()),
                    job_id,
                ),
            )

    def cancel(self, job_id: str, principal_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                """UPDATE jobs SET status='cancelled', completed_at=?, lease_expires_at=NULL
                   WHERE job_id=? AND principal_id=? AND status IN ('queued','running')""",
                (_iso(utcnow()), job_id, principal_id),
            )
            return cur.rowcount > 0

    def delete(self, job_id: str, principal_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM jobs WHERE job_id = ? AND principal_id = ?", (job_id, principal_id)
            )
            return cur.rowcount > 0

    def reclaim_expired_leases(self, max_attempts: int) -> int:
        now = _iso(utcnow())
        with self._lock:
            requeued = self._conn.execute(
                """UPDATE jobs SET status='queued', lease_expires_at=NULL
                   WHERE status='running' AND lease_expires_at IS NOT NULL
                     AND lease_expires_at < ? AND attempts < ?""",
                (now, max_attempts),
            ).rowcount
            self._conn.execute(
                """UPDATE jobs
                   SET status='failed', completed_at=?, lease_expires_at=NULL,
                       error=json_object('code','WORKER_LOST',
                                         'message','Worker lease expired and retries are exhausted')
                   WHERE status='running' AND lease_expires_at IS NOT NULL
                     AND lease_expires_at < ? AND attempts >= ?""",
                (now, now, max_attempts),
            )
        return requeued

    def expire_results(self, ttl_hours: int) -> int:
        if ttl_hours <= 0:
            return 0
        cutoff = _iso(datetime.now(UTC) - timedelta(hours=ttl_hours))
        with self._lock:
            return self._conn.execute(
                "UPDATE jobs SET status='expired' WHERE status='succeeded' AND completed_at < ?",
                (cutoff,),
            ).rowcount

    def close(self) -> None:
        with self._lock:
            self._conn.close()
