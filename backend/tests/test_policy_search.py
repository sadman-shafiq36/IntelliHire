import io

from fastapi.testclient import TestClient

from app.rag.policy_store import PolicyStore
from app.services import ai
from app.tools.policy_search import policy_search_tool, search_company_policy, search_many
from tests.conftest import auth_header

PASSAGES = [
    "Annual leave: employees receive twenty days of paid annual leave every year.",
    "Remote work: engineers may work remotely up to three days every week.",
    "Equal opportunity: we hire on merit, never on age, gender or religion.",
]


def _seed(store: PolicyStore) -> None:
    store.add_policy(7, "Handbook", PASSAGES, ai.embed_texts(PASSAGES))


def test_finds_the_most_relevant_passage(policy_store: PolicyStore):
    _seed(policy_store)
    hits = search_company_policy("how many days can engineers work remotely", k=3)
    assert hits[0].text == PASSAGES[1]
    assert hits[0].policy_id == 7
    assert hits[0].policy_title == "Handbook"
    assert hits[0].chunk_id == "policy-7-1"


def test_results_below_the_threshold_are_dropped(policy_store: PolicyStore):
    _seed(policy_store)
    assert search_company_policy("zebra xylophone quantum", k=3, min_score=0.9) == []


def test_blank_query_and_empty_store_return_nothing(policy_store: PolicyStore):
    assert search_company_policy("   ") == []
    assert search_company_policy("annual leave") == []


def test_search_many_deduplicates_and_keeps_the_best_score(policy_store: PolicyStore):
    _seed(policy_store)
    hits = search_many(["annual leave days", "paid annual leave every year"], k_per_query=3)
    ids = [h.chunk_id for h in hits]
    assert len(ids) == len(set(ids))
    assert ids[0] == "policy-7-0"
    assert hits == sorted(hits, key=lambda h: h.score, reverse=True)


def test_langchain_tool_returns_tagged_passages(policy_store: PolicyStore):
    _seed(policy_store)
    output = policy_search_tool.invoke({"query": "equal opportunity merit"})
    assert "[policy-7-2]" in output
    assert "(Handbook)" in output
    assert policy_search_tool.name == "policy_search_tool"


def test_search_endpoint(
    client: TestClient, interviewer_token: str, policy_store: PolicyStore, inline_jobs
):
    client.post(
        "/api/v1/policies",
        headers=auth_header(interviewer_token),
        files={"file": ("h.txt", io.BytesIO("\n\n".join(PASSAGES).encode()), "text/plain")},
    )
    response = client.get(
        "/api/v1/policies/search",
        params={"q": "remote work days"},
        headers=auth_header(interviewer_token),
    )
    assert response.status_code == 200
    hits = response.json()
    assert hits and "remotely" in hits[0]["text"]


def test_candidates_cannot_search(client: TestClient, candidate_token: str):
    response = client.get(
        "/api/v1/policies/search", params={"q": "leave"}, headers=auth_header(candidate_token)
    )
    assert response.status_code == 403
