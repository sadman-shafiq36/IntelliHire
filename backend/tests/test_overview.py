from fastapi.testclient import TestClient

from tests.conftest import auth_header, connect
from tests.test_job_posting import JOB


def _overview(client: TestClient, token: str) -> dict:
    response = client.get("/api/v1/overview", headers=auth_header(token))
    assert response.status_code == 200, response.text
    return response.json()


def test_empty_workspace(client: TestClient, interviewer_token: str):
    data = _overview(client, interviewer_token)
    assert data["pipeline"] == {
        "roles": 0,
        "described": 0,
        "live": 0,
        "applications": 0,
        "new_this_week": 0,
        "screened": 0,
        "shortlist_ready": 0,
    }
    assert data["attention"] == []
    assert data["recent_applications"] == []


def test_pipeline_and_attention_follow_each_job(
    client: TestClient, interviewer_token: str, providers, inline_jobs
):
    headers = auth_header(interviewer_token)
    connect(client, interviewer_token, "linkedin")
    connect(client, interviewer_token, "google")

    bare = client.post("/api/v1/jobs", json={**JOB, "title": "Bare role"}, headers=headers).json()
    live = client.post("/api/v1/jobs", json={**JOB, "title": "Live role"}, headers=headers).json()
    client.put(
        f"/api/v1/jobs/{live['id']}/description",
        json={"description_md": "# Live role\n\nBuild pipelines."},
        headers=headers,
    )
    client.post(f"/api/v1/jobs/{live['id']}/post", headers=headers)
    client.post(f"/api/v1/jobs/{live['id']}/post/publish", headers=headers)
    providers.add_response("r1")
    client.post(f"/api/v1/jobs/{live['id']}/applications/sync", headers=headers)

    data = _overview(client, interviewer_token)
    assert data["pipeline"] == {
        "roles": 2,
        "described": 1,
        "live": 1,
        "applications": 1,
        "new_this_week": 1,
        "screened": 0,
        "shortlist_ready": 0,
    }
    kinds = {(a["kind"], a["job_id"]) for a in data["attention"]}
    assert ("description", bare["id"]) in kinds
    # freshly synced, and its cv is waiting for screening
    assert ("sync", live["id"]) not in kinds
    assert ("screen", live["id"]) in kinds
    assert data["recent_applications"][0]["full_name"] == "Ada Lovelace"
    assert data["recent_applications"][0]["job_title"] == "Live role"
    assert data["live_jobs"][0]["applications"] == 1
    assert data["live_jobs"][0]["post_url"].startswith("https://www.linkedin.com/")


def test_unconnected_integrations_are_flagged(
    client: TestClient, interviewer_token: str, providers
):
    titles = [a["title"] for a in _overview(client, interviewer_token)["attention"]]
    assert "Connect LinkedIn" in titles
    assert "Connect Google" in titles


def test_candidates_cannot_see_the_overview(client: TestClient, candidate_token: str):
    assert client.get("/api/v1/overview", headers=auth_header(candidate_token)).status_code == 403
