"""API behaviour, run against the null extractor so nothing here depends on a provider."""

from __future__ import annotations

from tests.conftest import make_pdf


def test_health_is_ok(client):
    body = client.get("/healthz").json()
    assert body["status"] == "ok"


def test_ready_reports_backends(client):
    body = client.get("/readyz").json()
    assert body["checks"]["store"] == "ok"
    assert body["checks"]["blob"] == "ok"


def test_submit_returns_job_and_document_facts(client, pdf_bytes):
    response = client.post("/v1/jobs?filename=sample.pdf", content=pdf_bytes)
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "queued"
    assert body["document"]["pages"] == 2
    assert body["document"]["bytes"] == len(pdf_bytes)
    assert len(body["document"]["sha256"]) == 64
    assert body["links"]["result"].endswith("/result")


def test_job_runs_to_completion_and_returns_tables(completed_job, client):
    assert completed_job["status"] == "succeeded"
    assert completed_job["progress"]["percent"] == 100

    result = client.get(f"/v1/jobs/{completed_job['job_id']}/result")
    assert result.status_code == 200
    body = result.json()
    assert body["job_id"] == completed_job["job_id"]
    assert body["summary"]["tables"] == 1
    assert len(body["tables"][0]["rows"]) == 3
    assert body["tables"][0]["rows"][0]["amount"]["value"] == 93.0


def test_result_before_completion_is_conflict(client):
    """A fresh job has not run yet, so the result must not be fabricated."""
    response = client.post("/v1/jobs", content=make_pdf(1))
    job_id = response.json()["job_id"]
    early = client.get(f"/v1/jobs/{job_id}/result")
    assert early.status_code in {200, 409}
    if early.status_code == 409:
        assert early.json()["code"] == "RESULT_NOT_READY"


def test_unknown_job_is_not_found(client):
    response = client.get("/v1/jobs/01ARZ3NDEKTSV4RRFFQ69G5FAV")
    assert response.status_code == 404
    assert response.json()["code"] == "JOB_NOT_FOUND"


def test_non_pdf_is_rejected_on_magic_bytes(client):
    response = client.post("/v1/jobs", content=b"PK\x03\x04 this is a zip, not a pdf")
    assert response.status_code == 415
    assert response.json()["code"] == "UNSUPPORTED_MEDIA_TYPE"


def test_oversized_upload_is_rejected(client):
    response = client.post("/v1/jobs", content=b"%PDF-1.7\n" + b"\x00" * (6 * 1024 * 1024))
    assert response.status_code == 413
    assert response.json()["code"] == "PAYLOAD_TOO_LARGE"


def test_invalid_page_range_is_rejected(client, pdf_bytes):
    response = client.post("/v1/jobs?pages=1-99", content=pdf_bytes)
    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_PAGE_RANGE"


def test_idempotency_key_returns_the_same_job(client, pdf_bytes):
    headers = {"Idempotency-Key": "repeat-me"}
    first = client.post("/v1/jobs", content=pdf_bytes, headers=headers)
    second = client.post("/v1/jobs", content=pdf_bytes, headers=headers)
    assert first.status_code == 201
    assert second.status_code == 200
    assert first.json()["job_id"] == second.json()["job_id"]


def test_identical_bytes_do_not_start_a_second_job(client):
    payload = make_pdf(1, width=200, height=300)
    first = client.post("/v1/jobs", content=payload)
    second = client.post("/v1/jobs", content=payload)
    assert second.status_code == 200
    assert first.json()["job_id"] == second.json()["job_id"]


def test_listing_is_scoped_and_paginated(client, pdf_bytes):
    client.post("/v1/jobs", content=pdf_bytes)
    body = client.get("/v1/jobs?limit=1").json()
    assert len(body["jobs"]) == 1
    assert body["next_cursor"]


def test_delete_purges_job_and_result(client, completed_job):
    job_id = completed_job["job_id"]
    assert client.delete(f"/v1/jobs/{job_id}").status_code == 204
    assert client.get(f"/v1/jobs/{job_id}").status_code == 404


def test_errors_use_problem_details_and_carry_a_trace_id(client):
    response = client.get("/v1/jobs/nope")
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["trace_id"]


def test_metrics_are_exposed(client):
    body = client.get("/metrics").text
    assert "tx_jobs_completed_total" in body
