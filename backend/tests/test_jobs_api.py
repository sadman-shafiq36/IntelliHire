from fastapi.testclient import TestClient

from tests.conftest import auth_header

PROFILE = {
    "title": "  Senior Backend Engineer ",
    "department": "Engineering",
    "location": "Dhaka (hybrid)",
    "employment_type": "Full-time",
    "min_years_experience": 5,
    "summary": "Owns the order-routing service.",
    "requirements": [
        {"text": "Python web development", "category": "technical_skill", "is_mandatory": True},
        {"text": "PostgreSQL in production", "category": "technical_skill", "weight": 2},
        {"text": "BSc in Computer Science or equivalent", "category": "qualification"},
    ],
}


def _create(client: TestClient, token: str, profile=None) -> dict:
    response = client.post("/api/v1/jobs", json=profile or PROFILE, headers=auth_header(token))
    assert response.status_code == 201, response.text
    return response.json()


def test_create_and_read_a_job(client: TestClient, interviewer_token: str):
    job = _create(client, interviewer_token)
    assert job["title"] == "Senior Backend Engineer"
    assert job["status"] == "draft"
    assert job["description_md"] is None
    assert [r["text"] for r in job["requirements"]] == [
        "Python web development",
        "PostgreSQL in production",
        "BSc in Computer Science or equivalent",
    ]
    assert job["requirements"][0]["is_mandatory"] is True
    assert job["requirements"][1]["weight"] == 2

    fetched = client.get(f"/api/v1/jobs/{job['id']}", headers=auth_header(interviewer_token))
    assert fetched.json() == job


def test_list_summarises_each_job(client: TestClient, interviewer_token: str):
    _create(client, interviewer_token)
    listed = client.get("/api/v1/jobs", headers=auth_header(interviewer_token)).json()
    assert len(listed) == 1
    assert listed[0]["requirement_count"] == 3
    assert listed[0]["mandatory_count"] == 1
    assert listed[0]["has_description"] is False
    assert listed[0]["post_status"] is None
    assert listed[0]["application_count"] == 0


def test_update_keeps_requirement_ids_and_drops_removed_ones(
    client: TestClient, interviewer_token: str
):
    job = _create(client, interviewer_token)
    first, _, third = job["requirements"]
    profile = {
        **PROFILE,
        "title": "Staff Backend Engineer",
        "requirements": [
            {**first, "text": "Python web development (Django or FastAPI)"},
            {"id": third["id"], "text": third["text"], "category": "qualification"},
            {"text": "Mentoring other engineers", "category": "experience"},
        ],
    }
    response = client.put(
        f"/api/v1/jobs/{job['id']}", json=profile, headers=auth_header(interviewer_token)
    )
    assert response.status_code == 200, response.text
    updated = response.json()
    assert updated["title"] == "Staff Backend Engineer"
    ids = [r["id"] for r in updated["requirements"]]
    assert ids[:2] == [first["id"], third["id"]]
    assert updated["requirements"][0]["text"] == "Python web development (Django or FastAPI)"
    assert len(ids) == 3


def test_update_rejects_a_requirement_from_another_job(client: TestClient, interviewer_token: str):
    other = _create(client, interviewer_token)
    job = _create(client, interviewer_token)
    profile = {**PROFILE, "requirements": [{**other["requirements"][0]}]}
    response = client.put(
        f"/api/v1/jobs/{job['id']}", json=profile, headers=auth_header(interviewer_token)
    )
    assert response.status_code == 422


def test_a_job_needs_at_least_one_requirement(client: TestClient, interviewer_token: str):
    response = client.post(
        "/api/v1/jobs", json={**PROFILE, "requirements": []}, headers=auth_header(interviewer_token)
    )
    assert response.status_code == 422
    assert response.json()["error"]["details"]["problems"][0]["field"] == "requirements"


def test_save_description(client: TestClient, interviewer_token: str):
    job = _create(client, interviewer_token)
    response = client.put(
        f"/api/v1/jobs/{job['id']}/description",
        json={"description_md": "## About the role\n\nYou will own order routing."},
        headers=auth_header(interviewer_token),
    )
    assert response.status_code == 200
    assert response.json()["description_md"].startswith("## About the role")


def test_delete_a_job(client: TestClient, interviewer_token: str):
    job = _create(client, interviewer_token)
    headers = auth_header(interviewer_token)
    assert client.delete(f"/api/v1/jobs/{job['id']}", headers=headers).status_code == 204
    missing = client.get(f"/api/v1/jobs/{job['id']}", headers=headers)
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "not_found"


def test_candidates_cannot_see_or_change_jobs(
    client: TestClient, interviewer_token: str, candidate_token: str
):
    job = _create(client, interviewer_token)
    headers = auth_header(candidate_token)
    assert client.get("/api/v1/jobs", headers=headers).status_code == 403
    assert client.get(f"/api/v1/jobs/{job['id']}", headers=headers).status_code == 403
    assert client.post("/api/v1/jobs", json=PROFILE, headers=headers).status_code == 403
    assert client.delete(f"/api/v1/jobs/{job['id']}", headers=headers).status_code == 403
