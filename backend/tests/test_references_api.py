import io

from fastapi.testclient import TestClient

from app.rag.policy_store import PolicyStore
from app.tools.reference_search import search_references
from tests.conftest import auth_header

INDEXING_GUIDE = (
    "Database indexing\n\n"
    "A B-tree index speeds up equality and range lookups on a column, at the cost of slower "
    "writes and extra storage. Composite indexes follow the leftmost prefix rule.\n\n"
    "Query plans\n\n"
    "EXPLAIN ANALYZE shows whether PostgreSQL used an index scan or a sequential scan."
)
LEAVE_POLICY = "Annual leave\n\nEvery employee receives 20 days of paid annual leave per year."


def _upload(client: TestClient, token: str, path: str, body: str, title: str = ""):
    return client.post(
        f"/api/v1/{path}",
        headers=auth_header(token),
        data={"title": title},
        files={"file": ("doc.txt", io.BytesIO(body.encode()), "text/plain")},
    )


def test_references_are_indexed_apart_from_policies(
    client: TestClient,
    interviewer_token: str,
    policy_store: PolicyStore,
    reference_store: PolicyStore,
    inline_jobs,
):
    ref = _upload(client, interviewer_token, "references", INDEXING_GUIDE, "Indexing guide")
    pol = _upload(client, interviewer_token, "policies", LEAVE_POLICY, "Handbook")
    assert ref.status_code == 202 and pol.status_code == 202
    assert ref.json()["policy"]["kind"] == "reference"

    refs = client.get("/api/v1/references", headers=auth_header(interviewer_token)).json()
    pols = client.get("/api/v1/policies", headers=auth_header(interviewer_token)).json()
    assert [r["title"] for r in refs] == ["Indexing guide"]
    assert [p["title"] for p in pols] == ["Handbook"]
    assert refs[0]["status"] == "indexed"

    # each search only sees its own collection
    hits = client.get(
        "/api/v1/references/search",
        params={"q": "b-tree index range lookups"},
        headers=auth_header(interviewer_token),
    ).json()
    assert hits and all(h["policy_title"] == "Indexing guide" for h in hits)
    policy_hits = client.get(
        "/api/v1/policies/search",
        params={"q": "b-tree index range lookups"},
        headers=auth_header(interviewer_token),
    ).json()
    assert all(h["policy_title"] == "Handbook" for h in policy_hits)
    assert all(h.policy_title == "Indexing guide" for h in search_references(["b-tree index"]))


def test_a_reference_cannot_be_reached_through_the_policy_paths(
    client: TestClient,
    interviewer_token: str,
    policy_store: PolicyStore,
    reference_store: PolicyStore,
    inline_jobs,
):
    ref_id = _upload(client, interviewer_token, "references", INDEXING_GUIDE).json()["policy"]["id"]
    response = client.delete(f"/api/v1/policies/{ref_id}", headers=auth_header(interviewer_token))
    assert response.status_code == 404

    response = client.delete(f"/api/v1/references/{ref_id}", headers=auth_header(interviewer_token))
    assert response.status_code == 204
    assert reference_store.query([0.1] * 256, k=5) == []


def test_candidates_cannot_use_references(
    client: TestClient, candidate_token: str, reference_store: PolicyStore, inline_jobs
):
    assert _upload(client, candidate_token, "references", INDEXING_GUIDE).status_code == 403
    assert client.get("/api/v1/references", headers=auth_header(candidate_token)).status_code == 403
