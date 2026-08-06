"""Blob storage for uploads and results."""

from __future__ import annotations

from typing import Protocol


class BlobStore(Protocol):
    def ping(self) -> None: ...

    def put(self, key: str, data: bytes) -> None: ...

    def get(self, key: str) -> bytes: ...

    def exists(self, key: str) -> bool: ...

    def delete(self, key: str) -> None: ...

    def delete_prefix(self, prefix: str) -> int: ...


def upload_key(job_id: str) -> str:
    return f"{job_id}/upload.pdf"


def result_key(job_id: str) -> str:
    return f"{job_id}/result.json"


def page_image_key(job_id: str, page: int) -> str:
    return f"{job_id}/pages/{page:05d}.png"
