"""Job endpoints: submit, poll, fetch, list, cancel."""

from __future__ import annotations

import asyncio
import hashlib
import json

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from fastapi.responses import JSONResponse

from tx.api.deps import current_principal, get_blob, get_store
from tx.api.schemas import JobListResponse, JobResponse
from tx.blob.base import page_image_key, result_key, upload_key
from tx.core.config import settings
from tx.core.errors import (
    JobNotFound,
    PayloadTooLarge,
    ResultExpired,
    ResultNotReady,
)
from tx.core.ids import new_id
from tx.core.logging import get_logger
from tx.core.models import DocumentInfo, ExtractionMode, FlattenStyle, Job, JobOptions, JobStatus
from tx.pipeline.pdfdoc import inspect, parse_page_range
from tx.pipeline.render import render_page

log = get_logger(__name__)
router = APIRouter(prefix="/v1/jobs", tags=["jobs"])


async def _read_body(request: Request) -> bytes:
    """Read the body while enforcing the size cap, so an oversized upload is never buffered."""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > settings.max_upload_bytes:
        raise PayloadTooLarge(
            f"Declared {int(declared)} bytes, limit is {settings.max_upload_bytes}."
        )

    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > settings.max_upload_bytes:
            raise PayloadTooLarge(f"Limit is {settings.max_upload_bytes} bytes.")
        chunks.append(chunk)
    return b"".join(chunks)


@router.post("", status_code=201)
async def submit(
    request: Request,
    response: Response,
    filename: str | None = Query(default=None, max_length=255),
    pages: str | None = Query(default=None, max_length=200),
    mode: ExtractionMode | None = Query(default=None),
    flatten: FlattenStyle = Query(default="objects"),
    locale_hint: str | None = Query(default=None, max_length=20),
    currency: str | None = Query(default=None, max_length=8),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    principal: str = Depends(current_principal),
) -> JobResponse:
    data = await _read_body(request)
    store, blob = get_store(), get_blob()

    if idempotency_key:
        existing = await asyncio.to_thread(
            store.find_by_idempotency_key, principal, idempotency_key
        )
        if existing:
            response.status_code = 200
            return JobResponse.of(existing)

    total_pages = await asyncio.to_thread(inspect, data)
    if total_pages > settings.max_pages:
        raise PayloadTooLarge(f"{total_pages} pages, limit is {settings.max_pages}.")
    parse_page_range(pages, total_pages)  # validate now rather than inside the worker

    digest = hashlib.sha256(data).hexdigest()
    options = JobOptions(
        mode=mode or settings.extraction_mode_default,
        flatten=flatten,
        pages=pages,
        locale_hint=locale_hint or (settings.default_locale_hint or None),
        default_currency=currency or settings.default_currency,
    )
    if not idempotency_key:
        # Same bytes with a different page range is a different request, not a repeat.
        duplicate = await asyncio.to_thread(
            store.find_live_by_hash, principal, digest, options.model_dump_json()
        )
        if duplicate:
            response.status_code = 200
            return JobResponse.of(duplicate)

    job = Job(
        job_id=new_id(),
        principal_id=principal,
        document=DocumentInfo(filename=filename, sha256=digest, bytes=len(data), pages=total_pages),
        options=options,
        idempotency_key=idempotency_key,
    )

    await asyncio.to_thread(blob.put, upload_key(job.job_id), data)
    await asyncio.to_thread(store.create, job)
    log.info("job accepted", extra={"job_id": job.job_id, "pages": total_pages, "bytes": len(data)})
    return JobResponse.of(job)


@router.get("", response_model=JobListResponse)
async def list_jobs(
    status: JobStatus | None = Query(default=None),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    principal: str = Depends(current_principal),
) -> JobListResponse:
    jobs = await asyncio.to_thread(get_store().list, principal, status, cursor, limit)
    return JobListResponse(
        jobs=[JobResponse.of(j) for j in jobs],
        next_cursor=jobs[-1].job_id if len(jobs) == limit else None,
    )


@router.get("/{job_id}")
async def get_job(
    job_id: str, response: Response, principal: str = Depends(current_principal)
) -> JobResponse:
    job = await asyncio.to_thread(get_store().get, job_id, principal)
    if job is None:
        raise JobNotFound(f"No job {job_id!r}.")
    if not job.is_terminal:
        response.headers["Retry-After"] = str(max(1, int(settings.worker_poll_seconds * 2)))
    return JobResponse.of(job)


@router.get("/{job_id}/result")
async def get_result(job_id: str, principal: str = Depends(current_principal)) -> JSONResponse:
    job = await asyncio.to_thread(get_store().get, job_id, principal)
    if job is None:
        raise JobNotFound(f"No job {job_id!r}.")
    if job.status == JobStatus.EXPIRED:
        raise ResultExpired("The retention window has passed.")
    if job.status != JobStatus.SUCCEEDED:
        raise ResultNotReady(f"Job is {job.status.value}.")

    blob = get_blob()
    key = result_key(job_id)
    if not await asyncio.to_thread(blob.exists, key):
        raise ResultExpired("The result payload is no longer stored.")
    payload = await asyncio.to_thread(blob.get, key)
    return JSONResponse(
        content=json.loads(payload),
        headers={"Content-Disposition": f'attachment; filename="{job_id}.json"'},
    )


@router.delete("/{job_id}", status_code=204)
async def delete_job(job_id: str, principal: str = Depends(current_principal)) -> Response:
    store, blob = get_store(), get_blob()
    job = await asyncio.to_thread(store.get, job_id, principal)
    if job is None:
        raise JobNotFound(f"No job {job_id!r}.")
    await asyncio.to_thread(store.cancel, job_id, principal)
    await asyncio.to_thread(blob.delete_prefix, job_id)
    await asyncio.to_thread(store.delete, job_id, principal)
    log.info("job deleted", extra={"job_id": job_id})
    return Response(status_code=204)


async def _load_result(job_id: str, principal: str) -> dict:
    job = await asyncio.to_thread(get_store().get, job_id, principal)
    if job is None:
        raise JobNotFound(f"No job {job_id!r}.")
    if job.status == JobStatus.EXPIRED:
        raise ResultExpired("The retention window has passed.")
    if job.status != JobStatus.SUCCEEDED:
        raise ResultNotReady(f"Job is {job.status.value}.")
    blob = get_blob()
    key = result_key(job_id)
    if not await asyncio.to_thread(blob.exists, key):
        raise ResultExpired("The result payload is no longer stored.")
    return json.loads(await asyncio.to_thread(blob.get, key))


@router.get("/{job_id}/tables")
async def list_tables(
    job_id: str,
    include_suppressed: bool = Query(default=False),
    principal: str = Depends(current_principal),
) -> JSONResponse:
    """Table headlines only. A full result runs to megabytes; a browser wants an index."""
    payload = await _load_result(job_id, principal)
    rows = []
    for table in payload.get("tables", []):
        rows.append(_summarise(table, suppressed=False))
    if include_suppressed:
        for table in payload.get("suppressed_tables", []):
            rows.append(_summarise(table, suppressed=True))
    return JSONResponse(
        {
            "job_id": job_id,
            "document": payload.get("document", {}),
            "summary": payload.get("summary", {}),
            "extraction": payload.get("extraction", {}),
            "tables": rows,
        }
    )


def _summarise(table: dict, suppressed: bool) -> dict:
    return {
        "table_id": table.get("table_id"),
        "title": table.get("title"),
        "pages": table.get("pages", []),
        "n_rows": table.get("n_rows", 0),
        "n_cols": table.get("n_cols", 0),
        "rows_before_explosion": table.get("rows_before_explosion"),
        "columns": [c.get("label") for c in table.get("columns", [])][:8],
        "warnings": len(table.get("warnings", [])),
        "suppressed": suppressed,
    }


@router.get("/{job_id}/tables/{table_id}")
async def get_table(
    job_id: str, table_id: str, principal: str = Depends(current_principal)
) -> JSONResponse:
    payload = await _load_result(job_id, principal)
    for table in payload.get("tables", []) + payload.get("suppressed_tables", []):
        if table.get("table_id") == table_id:
            return JSONResponse(table)
    raise JobNotFound(f"No table {table_id!r} in job {job_id!r}.")


@router.get("/{job_id}/pages/{page}")
async def get_page_image(
    job_id: str, page: int, principal: str = Depends(current_principal)
) -> Response:
    """The rendered source page, so a reviewer can see where a value came from."""
    store, blob = get_store(), get_blob()
    job = await asyncio.to_thread(store.get, job_id, principal)
    if job is None:
        raise JobNotFound(f"No job {job_id!r}.")
    if page < 1 or (job.document.pages and page > job.document.pages):
        raise JobNotFound(f"Page {page} is outside this document.")

    cache_key = page_image_key(job_id, page)
    if await asyncio.to_thread(blob.exists, cache_key):
        png = await asyncio.to_thread(blob.get, cache_key)
    else:
        source = upload_key(job_id)
        if not await asyncio.to_thread(blob.exists, source):
            raise ResultExpired("The source document is no longer stored.")
        pdf = await asyncio.to_thread(blob.get, source)
        png = await asyncio.to_thread(render_page, pdf, page)
        await asyncio.to_thread(blob.put, cache_key, png)

    return Response(
        content=png,
        media_type="image/png",
        headers={"Cache-Control": "private, max-age=3600"},
    )
