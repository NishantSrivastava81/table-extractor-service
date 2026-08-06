"""Liveness and readiness.

Liveness answers "is the process alive". Readiness answers "can it do work", which means the
store and blob backend must respond. Neither endpoint touches a paid provider.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Response

from tx import __version__
from tx.core.config import settings

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@router.get("/readyz")
async def readyz(response: Response) -> dict[str, object]:
    from tx.api.deps import get_blob, get_store

    checks: dict[str, str] = {}
    try:
        await asyncio.to_thread(get_store().ping)
        checks["store"] = "ok"
    except Exception as exc:  # noqa: BLE001 - readiness reports, never raises
        checks["store"] = f"error: {type(exc).__name__}"
    try:
        await asyncio.to_thread(get_blob().ping)
        checks["blob"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["blob"] = f"error: {type(exc).__name__}"

    checks["ocr"] = "configured" if settings.ocr_configured else "not configured"
    checks["vision"] = "configured" if settings.vision_configured else "not configured"

    ready = all(v == "ok" for k, v in checks.items() if k in {"store", "blob"})
    if not ready:
        response.status_code = 503
    return {"status": "ready" if ready else "not ready", "checks": checks}
