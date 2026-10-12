import io
from pathlib import Path

from fastapi.testclient import TestClient

from app.models.job import CompanyPolicy
from app.rag.policy_store import PolicyStore
from tests.conftest import auth_header

LEAVE_POLICY = (
    "Annual leave\n\n"
    "Every full-time employee receives 20 days of paid annual leave per year.\n\n"
    "Remote work\n\n"
    "Engineering staff may work remotely up to three days a week.\n\n"
    "Equal opportunity\n\n"
    "We hire on merit and never on age, gender, religion or background."
)


def _upload(client: TestClient, token: str, name="handbook.txt", body=LEAVE_POLICY, title=""):
    return client.post(
        "/api/v1/policies",
        headers=auth_header(token),
        data={"title": title},
        files={"file": (name, io.BytesIO(body.encode()), "text/plain")},
    )


def test_upload_indexes_the_policy(
    client: TestClient, interviewer_token: str, policy_store: PolicyStore, inline_jobs
):
    response = _upload(client, interviewer_token, title="Employee handbook")
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["background_job_id"]
    assert inline_jobs[0]["error"] is None

    listed = client.get("/api/v1/policies", headers=auth_header(interviewer_token)).json()
    assert len(listed) == 1
    assert listed[0]["title"] == "Employee handbook"
    assert listed[0]["status"] == "indexed"
    assert listed[0]["chunk_count"] >= 1
    # storage path is internal and never returned
    assert "storage_path" not in listed[0]


def test_title_defaults_to_the_file_name(
    client: TestClient, interviewer_token: str, policy_store: PolicyStore, inline_jobs
):
    body = _upload(client, interviewer_token, name="leave-policy.txt").json()
    assert body["policy"]["title"] == "leave-policy"


def test_unsupported_file_type_is_rejected(
    client: TestClient, interviewer_token: str, policy_store: PolicyStore, inline_jobs
):
    response = _upload(client, interviewer_token, name="photo.png")
    assert response.status_code == 422
    assert "file types" in response.json()["error"]["message"]
    assert inline_jobs == []


def test_unreadable_file_is_marked_failed_with_a_reason(
    client: TestClient, interviewer_token: str, policy_store: PolicyStore, inline_jobs
):
    response = client.post(
        "/api/v1/policies",
        headers=auth_header(interviewer_token),
        files={"file": ("scan.docx", io.BytesIO(b"not a docx"), "application/octet-stream")},
    )
    assert response.status_code == 202
    policy = client.get("/api/v1/policies", headers=auth_header(interviewer_token)).json()[0]
    assert policy["status"] == "failed"
    assert "damaged" in policy["failure_reason"]
    assert inline_jobs[0]["error"] is not None


def test_delete_removes_chunks_and_file(
    client: TestClient, interviewer_token: str, policy_store: PolicyStore, inline_jobs, db_session
):
    policy_id = _upload(client, interviewer_token).json()["policy"]["id"]
    stored = Path(db_session.get(CompanyPolicy, policy_id).storage_path)
    assert stored.exists()

    response = client.delete(
        f"/api/v1/policies/{policy_id}", headers=auth_header(interviewer_token)
    )
    assert response.status_code == 204
    assert not stored.exists()
    assert policy_store.query([0.1] * 256, k=5) == []
    assert client.get("/api/v1/policies", headers=auth_header(interviewer_token)).json() == []


def test_reindex_does_not_duplicate_chunks(
    client: TestClient, interviewer_token: str, policy_store: PolicyStore, inline_jobs
):
    policy = _upload(client, interviewer_token).json()["policy"]
    first = client.get("/api/v1/policies", headers=auth_header(interviewer_token)).json()[0]

    response = client.post(
        f"/api/v1/policies/{policy['id']}/reindex", headers=auth_header(interviewer_token)
    )
    assert response.status_code == 202
    hits = policy_store.query([0.1] * 256, k=50)
    assert len(hits) == first["chunk_count"]


def test_candidates_cannot_upload_or_list_policies(
    client: TestClient, candidate_token: str, policy_store: PolicyStore, inline_jobs
):
    assert _upload(client, candidate_token).status_code == 403
    response = client.get("/api/v1/policies", headers=auth_header(candidate_token))
    assert response.status_code == 403
