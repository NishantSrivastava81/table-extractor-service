"""Deduplication: identical requests collapse, different requests do not."""

from __future__ import annotations

from tests.conftest import make_pdf


def test_same_bytes_and_same_options_return_the_same_job(client):
    payload = make_pdf(2, width=311, height=411)
    first = client.post("/v1/jobs", content=payload)
    second = client.post("/v1/jobs", content=payload)
    assert first.status_code == 201
    assert second.status_code == 200
    assert first.json()["job_id"] == second.json()["job_id"]


def test_same_bytes_with_a_different_page_range_is_a_new_job(client):
    """Asking for different pages is a different question, not a repeat."""
    payload = make_pdf(3, width=313, height=413)
    whole = client.post("/v1/jobs", content=payload)
    slice_ = client.post("/v1/jobs?pages=2", content=payload)
    assert whole.status_code == 201
    assert slice_.status_code == 201
    assert whole.json()["job_id"] != slice_.json()["job_id"]


def test_same_bytes_with_a_different_mode_is_a_new_job(client):
    payload = make_pdf(1, width=317, height=417)
    balanced = client.post("/v1/jobs?mode=balanced", content=payload)
    fast = client.post("/v1/jobs?mode=fast", content=payload)
    assert balanced.json()["job_id"] != fast.json()["job_id"]


def test_idempotency_key_still_wins_over_options(client):
    """An explicit key means the caller is retrying, whatever else they sent."""
    payload = make_pdf(1, width=319, height=419)
    headers = {"Idempotency-Key": "explicit-retry"}
    first = client.post("/v1/jobs", content=payload, headers=headers)
    second = client.post("/v1/jobs?mode=fast", content=payload, headers=headers)
    assert second.status_code == 200
    assert first.json()["job_id"] == second.json()["job_id"]
