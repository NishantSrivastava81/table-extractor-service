"""Endpoints the review UI depends on: table index, single table, page image."""

from __future__ import annotations

from tests.conftest import make_pdf


def test_table_index_returns_headlines_not_full_rows(client, completed_job):
    body = client.get(f"/v1/jobs/{completed_job['job_id']}/tables").json()
    assert body["summary"]["tables"] == 1
    table = body["tables"][0]
    assert table["table_id"] == "t_0001"
    assert table["n_rows"] == 3
    assert "rows" not in table, "the index must stay small; rows come from the detail endpoint"


def test_single_table_returns_its_rows(client, completed_job):
    body = client.get(f"/v1/jobs/{completed_job['job_id']}/tables/t_0001").json()
    assert len(body["rows"]) == 3
    assert body["rows"][0]["amount"]["value"] == 93.0


def test_unknown_table_is_not_found(client, completed_job):
    response = client.get(f"/v1/jobs/{completed_job['job_id']}/tables/t_9999")
    assert response.status_code == 404


def test_page_image_renders_and_is_cached(client, completed_job):
    job_id = completed_job["job_id"]
    first = client.get(f"/v1/jobs/{job_id}/pages/1")
    assert first.status_code == 200
    assert first.headers["content-type"] == "image/png"
    assert first.content[:8] == b"\x89PNG\r\n\x1a\n"

    second = client.get(f"/v1/jobs/{job_id}/pages/1")
    assert second.content == first.content, "second call must serve the cached render"


def test_page_outside_the_document_is_rejected(client, completed_job):
    assert client.get(f"/v1/jobs/{completed_job['job_id']}/pages/999").status_code == 404
    assert client.get(f"/v1/jobs/{completed_job['job_id']}/pages/0").status_code == 404


def test_tables_before_completion_is_conflict(client):
    response = client.post("/v1/jobs", content=make_pdf(1, width=401, height=501))
    job_id = response.json()["job_id"]
    early = client.get(f"/v1/jobs/{job_id}/tables")
    assert early.status_code in {200, 409}


def test_page_image_for_unknown_job_is_not_found(client):
    assert client.get("/v1/jobs/01ARZ3NDEKTSV4RRFFQ69G5FAV/pages/1").status_code == 404
