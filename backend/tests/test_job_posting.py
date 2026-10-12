import pytest
from fastapi.testclient import TestClient

from app.services.job_posting import compose_commentary
from tests.conftest import auth_header, connect

JOB = {
    "title": "Data Engineer",
    "department": "Data Platform",
    "location": "Singapore (hybrid)",
    "employment_type": "Full-time",
    "requirements": [
        {"text": "Python and SQL for data pipelines", "is_mandatory": True},
        {"text": "Apache Airflow"},
    ],
}


@pytest.fixture
def job(client: TestClient, interviewer_token: str) -> dict:
    headers = auth_header(interviewer_token)
    created = client.post("/api/v1/jobs", json=JOB, headers=headers).json()
    client.put(
        f"/api/v1/jobs/{created['id']}/description",
        json={"description_md": "# Data Engineer\n\n## About the role\n\nBuild pipelines."},
        headers=headers,
    )
    return created


@pytest.fixture
def connected(client: TestClient, interviewer_token: str, providers):
    connect(client, interviewer_token, "linkedin")
    connect(client, interviewer_token, "google")
    return providers


def _prepare(client, token, job_id):
    return client.post(f"/api/v1/jobs/{job_id}/post", headers=auth_header(token))


def test_preparing_copies_the_form_and_drafts_the_post(
    client: TestClient, interviewer_token: str, job, connected, inline_jobs
):
    response = _prepare(client, interviewer_token, job["id"])
    assert response.status_code == 202, response.text
    assert inline_jobs[-1]["error"] is None, inline_jobs[-1]["error"]

    post = client.get(
        f"/api/v1/jobs/{job['id']}/post", headers=auth_header(interviewer_token)
    ).json()
    assert post["status"] == "draft"
    assert post["form_url"] == "https://docs.google.com/forms/d/e/form-1-public/viewform"
    assert post["form_edit_url"] == "https://docs.google.com/forms/d/form-1/edit"
    assert "Data Engineer" in post["text"]
    assert post["hashtags"]

    assert connected.copies == 1
    assert connected.form_info["title"] == "Apply: Data Engineer"
    assert "Python and SQL for data pipelines" in connected.form_info["description"]
    assert connected.accepting["form-1"] is True


def test_publishing_posts_to_linkedin_with_the_form_link(
    client: TestClient, interviewer_token: str, job, connected, inline_jobs
):
    headers = auth_header(interviewer_token)
    _prepare(client, interviewer_token, job["id"])
    client.put(
        f"/api/v1/jobs/{job['id']}/post/text",
        json={"text": "We're hiring a Data Engineer (hybrid) to build pipelines. Apply below!"},
        headers=headers,
    )

    response = client.post(f"/api/v1/jobs/{job['id']}/post/publish", headers=headers)
    assert response.status_code == 200, response.text
    post = response.json()
    assert post["status"] == "posted"
    assert post["post_url"] == "https://www.linkedin.com/feed/update/urn:li:share:999/"

    sent = connected.posts[0]
    assert sent["author"] == "urn:li:person:member-42"
    assert sent["content"]["article"]["source"] == post["form_url"]
    assert sent["content"]["article"]["title"] == "Apply: Data Engineer"
    # reserved characters escaped for linkedin
    assert "\\(hybrid\\)" in sent["commentary"]
    assert "{hashtag|\\#|hiring}" in sent["commentary"]
    assert connected.post_headers[0]["linkedin-version"] == "202609"

    job_now = client.get(f"/api/v1/jobs/{job['id']}", headers=headers).json()
    assert job_now["status"] == "open"


def test_closing_stops_the_form_accepting_responses(
    client: TestClient, interviewer_token: str, job, connected, inline_jobs
):
    headers = auth_header(interviewer_token)
    _prepare(client, interviewer_token, job["id"])
    client.post(f"/api/v1/jobs/{job['id']}/post/publish", headers=headers)

    response = client.post(f"/api/v1/jobs/{job['id']}/post/close", headers=headers)
    assert response.json()["status"] == "closed"
    assert connected.accepting["form-1"] is False
    assert client.get(f"/api/v1/jobs/{job['id']}", headers=headers).json()["status"] == "closed"

    # a closed post can be replaced by a new one
    assert _prepare(client, interviewer_token, job["id"]).status_code == 202


def test_only_one_active_post_per_job(
    client: TestClient, interviewer_token: str, job, connected, inline_jobs
):
    _prepare(client, interviewer_token, job["id"])
    response = _prepare(client, interviewer_token, job["id"])
    assert response.status_code == 409


def test_a_template_without_a_cv_question_fails_clearly(
    client: TestClient, interviewer_token: str, job, connected, inline_jobs
):
    connected.template_has_file_question = False
    _prepare(client, interviewer_token, job["id"])
    post = client.get(
        f"/api/v1/jobs/{job['id']}/post", headers=auth_header(interviewer_token)
    ).json()
    assert post["status"] == "failed"
    assert "file upload question" in post["failure_reason"]


def test_preparing_needs_a_description(
    client: TestClient, interviewer_token: str, connected, inline_jobs
):
    created = client.post("/api/v1/jobs", json=JOB, headers=auth_header(interviewer_token)).json()
    response = _prepare(client, interviewer_token, created["id"])
    assert response.status_code == 422
    assert "job description first" in response.json()["error"]["message"]


def test_preparing_needs_google_connected(
    client: TestClient, interviewer_token: str, job, providers, inline_jobs
):
    response = _prepare(client, interviewer_token, job["id"])
    assert response.status_code == 503
    assert "Google is not connected" in response.json()["error"]["message"]


def test_publishing_needs_linkedin_connected(
    client: TestClient, interviewer_token: str, job, providers, inline_jobs
):
    connect(client, interviewer_token, "google")
    _prepare(client, interviewer_token, job["id"])
    response = client.post(
        f"/api/v1/jobs/{job['id']}/post/publish", headers=auth_header(interviewer_token)
    )
    assert "LinkedIn is not connected" in response.json()["error"]["message"]


def test_a_linkedin_rejection_says_to_reconnect_and_keeps_the_draft(
    client: TestClient, interviewer_token: str, job, connected, inline_jobs
):
    headers = auth_header(interviewer_token)
    _prepare(client, interviewer_token, job["id"])
    connected.linkedin_post_status = 401

    response = client.post(f"/api/v1/jobs/{job['id']}/post/publish", headers=headers)
    assert response.status_code == 503
    assert "Reconnect LinkedIn" in response.json()["error"]["message"]
    post = client.get(f"/api/v1/jobs/{job['id']}/post", headers=headers).json()
    assert post["status"] == "draft"


def test_a_published_post_cannot_be_edited(
    client: TestClient, interviewer_token: str, job, connected, inline_jobs
):
    headers = auth_header(interviewer_token)
    _prepare(client, interviewer_token, job["id"])
    client.post(f"/api/v1/jobs/{job['id']}/post/publish", headers=headers)
    response = client.put(
        f"/api/v1/jobs/{job['id']}/post/text", json={"text": "x" * 40}, headers=headers
    )
    assert response.status_code == 409


def test_commentary_escaping():
    text = "Join us (remote) & build [things] with C# - email me@x.com_now"
    out = compose_commentary(text, ["hiring", "data-eng"])
    assert "\\(remote\\)" in out
    assert "\\[things\\]" in out
    assert "C\\#" in out
    assert "me\\@x.com\\_now" in out
    assert out.endswith("{hashtag|\\#|hiring} {hashtag|\\#|dataeng}")


def test_candidates_cannot_post(client: TestClient, candidate_token: str, job):
    assert _prepare(client, candidate_token, job["id"]).status_code == 403
