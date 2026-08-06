"""Wiring. Single place where concrete backends are chosen from configuration."""

from __future__ import annotations

from functools import lru_cache

from fastapi import Header

from tx.blob.base import BlobStore
from tx.blob.local import LocalBlobStore
from tx.core.config import settings
from tx.pipeline.base import Extractor
from tx.store.base import JobStore
from tx.store.sqlite import SqliteJobStore

#: v1 has no authentication. Every request resolves to this principal, and every query still
#: filters on it, so turning auth on later is a change to this function alone.
LOCAL_PRINCIPAL = "local"


@lru_cache(maxsize=1)
def get_store() -> JobStore:
    return SqliteJobStore(settings.db_path)


@lru_cache(maxsize=1)
def get_blob() -> BlobStore:
    return LocalBlobStore(settings.blob_dir)


@lru_cache(maxsize=1)
def get_extractor() -> Extractor:
    if settings.extractor == "null":
        from tx.pipeline.null import NullExtractor

        return NullExtractor()
    from tx.pipeline.orchestrator import PipelineExtractor

    return PipelineExtractor()


async def current_principal(x_principal: str | None = Header(default=None)) -> str:
    """Placeholder for authentication. Returns a fixed principal until auth lands."""
    return x_principal or LOCAL_PRINCIPAL
