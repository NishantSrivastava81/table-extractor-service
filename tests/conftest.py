"""Test setup. Environment is configured before `tx` is imported, because settings are read once."""

from __future__ import annotations

import io
import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="tx-test-")
os.environ["DATA_DIR"] = _TMP
os.environ["EXTRACTOR"] = "null"
os.environ["RUN_WORKER_IN_PROCESS"] = "true"
os.environ["LOG_FORMAT"] = "text"
os.environ["LOG_LEVEL"] = "WARNING"
os.environ["WORKER_POLL_SECONDS"] = "0.05"
# A stranded job must return to the queue quickly, or a test that starts its own worker waits
# out the production lease.
os.environ["WORKER_LEASE_SECONDS"] = "10"
os.environ["MAX_UPLOAD_BYTES"] = "5242880"

import pypdfium2 as pdfium  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from tx.core.config import settings  # noqa: E402

# A test run must never reach a paid provider. Failing here is cheaper than finding out on an
# invoice, and a stray .env in the project root would otherwise do exactly that.
assert settings.extractor == "null", (
    f"tests must run with EXTRACTOR=null, got {settings.extractor!r}"
)


def make_pdf(pages: int = 1, width: float = 595.0, height: float = 842.0) -> bytes:
    doc = pdfium.PdfDocument.new()
    for _ in range(pages):
        doc.new_page(width, height)
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


@pytest.fixture(scope="session")
def pdf_bytes() -> bytes:
    return make_pdf(2)


@pytest.fixture()
def client():
    from tx.api.app import app

    with TestClient(app) as c:
        yield c


@pytest.fixture()
def completed_job(client):
    """Submit a job and poll until it leaves a non-terminal state.

    Page size is varied so the bytes are unique: identical uploads are deduplicated by hash,
    which would otherwise hand this fixture a job another test already ran.
    """
    import random
    import time

    payload = make_pdf(1, width=random.uniform(100, 900), height=random.uniform(100, 900))
    response = client.post("/v1/jobs", content=payload)
    assert response.status_code == 201, response.text
    job_id = response.json()["job_id"]

    deadline = time.time() + 40
    while time.time() < deadline:
        state = client.get(f"/v1/jobs/{job_id}").json()
        if state["status"] in {"succeeded", "failed", "cancelled"}:
            return state
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish in time")
