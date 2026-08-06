"""Worker loop: claim a job, run the extractor, store the result.

Runs either as an asyncio task inside the API process or as its own container. The only
difference is where `run_forever` is called from.
"""

from __future__ import annotations

import asyncio
import contextlib
import time

from tx.blob.base import result_key, upload_key
from tx.core.config import settings
from tx.core.logging import get_logger, job_id_var
from tx.core.models import Job, JobError, JobStatus, Progress
from tx.metrics import (
    JOB_DURATION,
    JOBS_COMPLETED,
    QUEUE_DEPTH,
    STAGE_DURATION,
    VERBATIM_SCORE,
)

log = get_logger(__name__)


class Worker:
    def __init__(self, store=None, blob=None, extractor=None) -> None:
        from tx.api.deps import get_blob, get_extractor, get_store

        self._store = store or get_store()
        self._blob = blob or get_blob()
        self._extractor = extractor or get_extractor()
        self._stopping = asyncio.Event()

    def stop(self) -> None:
        self._stopping.set()

    async def run_forever(self) -> None:
        log.info("worker started", extra={"extractor": type(self._extractor).__name__})
        while not self._stopping.is_set():
            try:
                did_work = await self._tick()
            except Exception:  # noqa: BLE001 - a worker must not die on one bad job
                log.exception("worker tick failed")
                did_work = False
            if not did_work:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(
                        self._stopping.wait(), timeout=settings.worker_poll_seconds
                    )
        log.info("worker stopped")

    async def _tick(self) -> bool:
        await asyncio.to_thread(self._store.reclaim_expired_leases, settings.max_attempts)
        if settings.result_ttl_hours > 0 and hasattr(self._store, "expire_results"):
            await asyncio.to_thread(self._store.expire_results, settings.result_ttl_hours)

        job = await asyncio.to_thread(self._store.claim_next, settings.worker_lease_seconds)
        if job is None:
            QUEUE_DEPTH.set(0)
            return False

        token = job_id_var.set(job.job_id)
        try:
            await self._process(job)
        finally:
            job_id_var.reset(token)
        return True

    async def _process(self, job: Job) -> None:
        started = time.perf_counter()
        log.info("job started", extra={"attempt": job.attempts, "mode": job.options.mode})

        heartbeat = asyncio.create_task(self._heartbeat(job.job_id))
        try:
            pdf = await asyncio.to_thread(self._blob.get, upload_key(job.job_id))

            stage_started = {"t": time.perf_counter(), "stage": "start"}

            def on_progress(progress: Progress) -> None:
                now = time.perf_counter()
                if progress.stage != stage_started["stage"]:
                    STAGE_DURATION.labels(stage=stage_started["stage"]).observe(
                        now - stage_started["t"]
                    )
                    stage_started.update(t=now, stage=progress.stage)
                # Runs on the extractor's thread, so the write is ordered with the work that
                # produced it. Scheduling it onto the loop instead let the final update land
                # after the job was already marked succeeded.
                self._store.set_progress(job.job_id, progress)

            result = await asyncio.to_thread(self._extractor.extract, pdf, job, on_progress)

            await asyncio.to_thread(
                self._blob.put,
                result_key(job.job_id),
                result.model_dump_json(indent=2).encode(),
            )
            if settings.delete_upload_on_success:
                await asyncio.to_thread(self._blob.delete, upload_key(job.job_id))

            await asyncio.to_thread(self._store.finish, job.job_id, JobStatus.SUCCEEDED)
            JOBS_COMPLETED.labels(status="succeeded").inc()
            VERBATIM_SCORE.observe(result.summary.verbatim_score)
            log.info(
                "job succeeded",
                extra={
                    "tables": result.summary.tables,
                    "rows": result.summary.rows,
                    "cells": result.summary.cells_populated,
                    "duration_s": round(time.perf_counter() - started, 2),
                },
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - failure is a job outcome, not a crash
            log.exception("job failed")
            await asyncio.to_thread(
                self._store.finish,
                job.job_id,
                JobStatus.FAILED,
                JobError(code=type(exc).__name__, message=str(exc)[:500]),
            )
            JOBS_COMPLETED.labels(status="failed").inc()
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat
            JOB_DURATION.labels(mode=job.options.mode).observe(time.perf_counter() - started)

    async def _heartbeat(self, job_id: str) -> None:
        interval = max(5.0, settings.worker_lease_seconds / 3)
        while True:
            await asyncio.sleep(interval)
            await asyncio.to_thread(self._store.heartbeat, job_id, settings.worker_lease_seconds)


async def main() -> None:
    from tx.core.logging import configure

    configure(settings.log_level, settings.log_format)
    await Worker().run_forever()


if __name__ == "__main__":
    asyncio.run(main())
