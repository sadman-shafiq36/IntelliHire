from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models.candidate import Candidate, CvDocument
from tests.conftest import auth_header, connect
from tests.fake_providers import PDF_BYTES
from tests.test_job_posting import JOB


@pytest.fixture
def posted_job(client: TestClient, interviewer_token: str, providers, inline_jobs) -> dict:
    headers = auth_header(interviewer_token)
    connect(client, interviewer_token, "linkedin")
    connect(client, interviewer_token, "google")
    job = client.post("/api/v1/jobs", json=JOB, headers=headers).json()
    client.put(
        f"/api/v1/jobs/{job['id']}/description",
        json={"description_md": "# Data Engineer\n\nBuild pipelines for our clients."},
        headers=headers,
    )
    client.post(f"/api/v1/jobs/{job['id']}/post", headers=headers)
    client.post(f"/api/v1/jobs/{job['id']}/post/publish", headers=headers)
    return job


def _sync(client, token, job_id, inline_jobs) -> dict:
    response = client.post(f"/api/v1/jobs/{job_id}/applications/sync", headers=auth_header(token))
    assert response.status_code == 202, response.text
    assert inline_jobs[-1]["error"] is None, inline_jobs[-1]["error"]
    return inline_jobs[-1]["result"]


def _list(client, token, job_id) -> list[dict]:
    return client.get(f"/api/v1/jobs/{job_id}/applications", headers=auth_header(token)).json()


def test_sync_imports_applicants_and_their_cvs(
    client: TestClient, interviewer_token: str, posted_job, providers, inline_jobs, db_session
):
    providers.add_response(
        "r1",
        extra={
            "q_phone": "+65 5555 0101",
            "q_location": "Singapore",
            "q_exp": "6",
            "q_li": "https://linkedin.com/in/ada",
            "q_cover": "I love pipelines.",
        },
    )
    result = _sync(client, interviewer_token, posted_job["id"], inline_jobs)
    assert result["imported"] == 1
    assert result["failed"] == 0

    [row] = _list(client, interviewer_token, posted_job["id"])
    assert row["full_name"] == "Ada Lovelace"
    assert row["email"] == "ada@example.com"
    assert row["phone"] == "+65 5555 0101"
    assert row["location"] == "Singapore"
    assert row["years_experience"] == "6"
    assert row["linkedin_url"] == "https://linkedin.com/in/ada"
    assert row["cover_note"] == "I love pipelines."
    assert row["source"] == "google_form"
    assert row["cv_filename"] == "ada-cv.pdf"
    # waits for screening in module 5
    assert row["cv_status"] == "pending"

    cv = db_session.scalar(select(CvDocument))
    assert Path(cv.storage_path).read_bytes() == PDF_BYTES
    assert cv.job_id == posted_job["id"]

    download = client.get(
        f"/api/v1/applications/{row['id']}/cv", headers=auth_header(interviewer_token)
    )
    assert download.status_code == 200
    assert download.content == PDF_BYTES


def test_a_second_sync_imports_only_new_responses(
    client: TestClient, interviewer_token: str, posted_job, providers, inline_jobs
):
    providers.add_response("r1")
    _sync(client, interviewer_token, posted_job["id"], inline_jobs)
    providers.add_response("r2", email="grace@example.com", name="Grace Hopper")

    result = _sync(client, interviewer_token, posted_job["id"], inline_jobs)
    assert result["imported"] == 1
    assert result["already_imported"] == 1
    assert len(_list(client, interviewer_token, posted_job["id"])) == 2


def test_one_bad_response_does_not_stop_the_others(
    client: TestClient, interviewer_token: str, posted_job, providers, inline_jobs, db_session
):
    providers.add_response("good-1", email="a@example.com", name="A One")
    providers.add_response("no-cv", email="b@example.com", name="B Two", cv=None)
    providers.add_response(
        "wrong-type", email="c@example.com", name="C Three", cv=("cv.png", "image/png", b"png")
    )
    providers.add_response("no-consent", email="d@example.com", name="D Four", consent=False)
    providers.add_response("good-2", email="e@example.com", name="E Five")

    result = _sync(client, interviewer_token, posted_job["id"], inline_jobs)
    assert result["imported"] == 2
    reasons = {item["item_ref"]: item["reason"] for item in result["failed_items"]}
    assert "no CV" in reasons["no-cv"]
    assert "PDF or Word" in reasons["wrong-type"]
    assert "did not agree" in reasons["no-consent"]

    names = {row["full_name"] for row in _list(client, interviewer_token, posted_job["id"])}
    assert names == {"A One", "E Five"}
    # nothing half-saved for the failed ones
    emails = set(db_session.scalars(select(Candidate.email)))
    assert emails == {"a@example.com", "e@example.com"}


def test_a_returning_applicant_is_matched_by_email(
    client: TestClient, interviewer_token: str, posted_job, providers, inline_jobs, db_session
):
    db_session.add(Candidate(full_name="Ada L.", email="ADA@example.com"))
    db_session.commit()
    providers.add_response("r1", email="ada@example.com")

    _sync(client, interviewer_token, posted_job["id"], inline_jobs)
    assert db_session.scalars(select(Candidate)).all().__len__() == 1


def test_sync_without_a_form_explains_why(
    client: TestClient, interviewer_token: str, providers, inline_jobs
):
    connect(client, interviewer_token, "google")
    job = client.post("/api/v1/jobs", json=JOB, headers=auth_header(interviewer_token)).json()
    client.post(
        f"/api/v1/jobs/{job['id']}/applications/sync", headers=auth_header(interviewer_token)
    )
    assert "no application form" in inline_jobs[-1]["error"].message


def test_candidates_cannot_see_applications_or_cvs(
    client: TestClient,
    interviewer_token: str,
    candidate_token: str,
    posted_job,
    providers,
    inline_jobs,
):
    providers.add_response("r1")
    _sync(client, interviewer_token, posted_job["id"], inline_jobs)
    [row] = _list(client, interviewer_token, posted_job["id"])

    headers = auth_header(candidate_token)
    assert (
        client.get(f"/api/v1/jobs/{posted_job['id']}/applications", headers=headers).status_code
        == 403
    )
    assert client.get(f"/api/v1/applications/{row['id']}/cv", headers=headers).status_code == 403
    response = client.post(f"/api/v1/jobs/{posted_job['id']}/applications/sync", headers=headers)
    assert response.status_code == 403
