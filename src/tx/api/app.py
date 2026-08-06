"""FastAPI application: middleware, error rendering, lifespan, routes."""

from __future__ import annotations

import asyncio
import contextlib
import secrets
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from tx import __version__
from tx.api import routes_health, routes_jobs
from tx.core.config import settings
from tx.core.errors import TxError
from tx.core.logging import configure, get_logger, trace_id_var

log = get_logger(__name__)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure(settings.log_level, settings.log_format)
    settings.data_dir.mkdir(parents=True, exist_ok=True)

    from tx.api.deps import get_blob, get_store

    get_store()
    get_blob()

    if settings.host not in {"127.0.0.1", "localhost", "::1"}:
        log.warning(
            "service is bound beyond localhost and has no authentication",
            extra={"host": settings.host},
        )

    worker_task: asyncio.Task | None = None
    worker = None
    if settings.run_worker_in_process:
        from tx.worker.runner import Worker

        worker = Worker()
        worker_task = asyncio.create_task(worker.run_forever())

    log.info("service ready", extra={"version": __version__, "extractor": settings.extractor})
    try:
        yield
    finally:
        if worker and worker_task:
            worker.stop()
            with contextlib.suppress(asyncio.CancelledError, TimeoutError):
                await asyncio.wait_for(worker_task, timeout=30)


app = FastAPI(
    title="Table Extractor Service",
    version=__version__,
    description="Submit a PDF, poll a job, receive flattened tables as JSON.",
    lifespan=lifespan,
)

app.include_router(routes_health.router)
app.include_router(routes_jobs.router)

if settings.cors_origins:
    from fastapi.middleware.cors import CORSMiddleware

    # Only for a UI dev server on another port. Serving the built UI from this app needs none.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["*"],
        expose_headers=["X-Request-Id", "Retry-After"],
    )

_UI_DIR = Path(__file__).resolve().parents[3] / "ui" / "dist"
if _UI_DIR.is_dir():
    from fastapi.staticfiles import StaticFiles

    app.mount("/ui", StaticFiles(directory=str(_UI_DIR), html=True), name="ui")


@app.middleware("http")
async def add_trace_id(request: Request, call_next):
    trace_id = request.headers.get("x-request-id") or secrets.token_hex(8)
    token = trace_id_var.set(trace_id)
    try:
        response = await call_next(request)
    finally:
        trace_id_var.reset(token)
    response.headers["X-Request-Id"] = trace_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    # The UI is same-origin and needs its own scripts, styles and the page images.
    if not request.url.path.startswith("/ui"):
        response.headers["Content-Security-Policy"] = "default-src 'none'"
    return response


@app.exception_handler(TxError)
async def handle_tx_error(request: Request, exc: TxError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status,
        content=exc.to_problem(str(request.url.path), trace_id_var.get()),
        media_type="application/problem+json",
    )


@app.exception_handler(RequestValidationError)
async def handle_validation(request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "type": "about:blank#validation-error",
            "title": "Request is not valid",
            "status": 422,
            "code": "VALIDATION_ERROR",
            "instance": str(request.url.path),
            "errors": [
                {"loc": list(e.get("loc", [])), "msg": e.get("msg", "")} for e in exc.errors()
            ],
            "trace_id": trace_id_var.get(),
        },
        media_type="application/problem+json",
    )


@app.exception_handler(Exception)
async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
    log.exception("unhandled error", extra={"path": request.url.path})
    return JSONResponse(
        status_code=500,
        content={
            "type": "about:blank#internal",
            "title": "Internal error",
            "status": 500,
            "code": "INTERNAL",
            "instance": str(request.url.path),
            "trace_id": trace_id_var.get(),
        },
        media_type="application/problem+json",
    )


@app.get("/metrics", include_in_schema=False)
async def metrics() -> PlainTextResponse:
    return PlainTextResponse(generate_latest().decode(), media_type=CONTENT_TYPE_LATEST)
