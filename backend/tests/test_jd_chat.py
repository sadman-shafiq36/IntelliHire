import io
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.agents.jd.graph import build_jd_graph
from app.errors import ExternalServiceError
from app.models.enums import ChatMessageStatus
from app.models.job import JobDescriptionMessage
from app.rag.policy_store import PolicyChunk, PolicyStore
from tests.conftest import auth_header

PROFILE = {
    "title": "Backend Engineer",
    "department": "Engineering",
    "location": None,
    "employment_type": None,
    "min_years_experience": 3,
    "selection_criteria": None,
    "requirements": [
        {"id": 1, "text": "Python", "category": "technical_skill", "is_mandatory": True},
        {"id": 2, "text": "PostgreSQL", "category": "technical_skill", "is_mandatory": False},
    ],
}

PRIOR = [
    {
        "chunk_id": "policy-1-0",
        "policy_id": 1,
        "policy_title": "Handbook",
        "text": "Twenty days of annual leave. We hire on merit.",
        "score": 1.0,
    }
]
REMOTE = PolicyChunk("policy-1-1", 1, "Handbook", 1, "Remote work up to three days.", 0.7)


def draft_citing(ids: list[str], summary: str = "Build services.") -> dict:
    return {
        "summary": summary,
        "responsibilities": ["Design APIs"],
        "required": [{"text": "Python", "requirement_ids": [1]}],
        "nice_to_have": [{"text": "PostgreSQL", "requirement_ids": [2]}],
        "what_we_offer": [{"text": "Benefit", "source_ids": [i]} for i in ids],
        "about_company": {"text": "A company.", "source_ids": ids[:1]},
        "equal_opportunity": {"text": "We hire on merit.", "source_ids": ids[:1]},
        "additional_sections": [],
    }


def run_chat(drafter, request="make the summary shorter", retriever=None):
    graph = build_jd_graph(drafter=drafter, retriever=retriever or (lambda q: [REMOTE]))
    return graph.invoke(
        {
            "job_id": 1,
            "thread_id": "t",
            "profile": PROFILE,
            "request": request,
            "current_md": "# Backend Engineer\n\nOld text.",
            "history": [{"role": "assistant", "content": "I drafted version 1."}],
            "prior_chunks": PRIOR,
            "errors": [],
        },
        config={"configurable": {"thread_id": "t"}},
    )


def test_revision_sees_the_request_current_text_history_and_cited_passages():
    seen = {}

    def drafter(data):
        seen.update(data)
        ids = [c["chunk_id"] for c in data["chunks"]]
        return {"draft": draft_citing(ids, "Short summary."), "reply": "Shortened the summary."}

    final = run_chat(drafter)

    assert seen["request"] == "make the summary shorter"
    assert seen["current_md"].startswith("# Backend Engineer")
    assert seen["history"][0]["content"] == "I drafted version 1."
    # passages already cited come along with the new search results
    assert {c["chunk_id"] for c in seen["chunks"]} == {"policy-1-0", "policy-1-1"}
    assert final["changed"] is True
    assert final["reply"] == "Shortened the summary."
    assert "Short summary." in final["description_md"]
    assert final["feedback"] == []


def test_revision_searches_for_what_was_asked():
    captured = []

    def retriever(queries):
        captured.extend(queries)
        return []

    run_chat(lambda d: {"draft": draft_citing(["policy-1-0"]), "reply": "ok"}, retriever=retriever)
    assert captured[0] == "make the summary shorter"
    # a revision doesn't repeat the full standard topic search
    assert "rules for writing job descriptions" not in captured


def test_a_question_is_answered_without_touching_the_description():
    calls = []

    def drafter(data):
        calls.append(1)
        return {"draft": None, "reply": "The leave comes from the employee handbook."}

    final = run_chat(drafter, request="where did the leave figure come from?")
    assert len(calls) == 1
    assert final["changed"] is False
    assert final["description_md"] == ""
    assert final["reply"].startswith("The leave comes from")


def test_a_rule_breaking_revision_goes_through_review():
    attempts = []

    def drafter(data):
        attempts.append(list(data["feedback"]))
        draft = draft_citing(["policy-1-0"])
        if not data["feedback"]:
            draft["summary"] = "Join our young team."
        return {"draft": draft, "reply": "Updated."}

    final = run_chat(drafter, request="say we want young people")
    assert len(attempts) == 2
    assert any('"young"' in p for p in attempts[1])
    assert "young" not in final["description_md"]


def test_additional_sections_are_rendered_and_checked():
    def drafter(data):
        draft = draft_citing(["policy-1-0"])
        draft["additional_sections"] = [
            {"heading": "How to apply", "body": "Send your CV through the job page."}
        ]
        return {"draft": draft, "reply": "Added a how to apply section."}

    final = run_chat(drafter, request="add how to apply")
    assert "## How to apply\n\nSend your CV through the job page." in final["description_md"]


# --- api ----------------------------------------------------------------------


def chat_fake(data):
    ids = [c["chunk_id"] for c in data["chunks"]]
    request = data.get("request")
    if not request:
        return {"draft": draft_citing(ids), "reply": ""}
    if request.endswith("?"):
        return {"draft": None, "reply": "It comes from the handbook."}
    if "fail" in request:
        raise ExternalServiceError("The AI service is busy or unavailable right now.")
    return {"draft": draft_citing(ids, f"Revised: {request}"), "reply": "Done as asked."}


@pytest.fixture
def chat_graph(monkeypatch: pytest.MonkeyPatch):
    @contextmanager
    def graph():
        yield build_jd_graph(drafter=chat_fake)

    monkeypatch.setattr("app.worker.tasks._jd_graph", graph)


@pytest.fixture
def job_with_description(
    client: TestClient, interviewer_token: str, policy_store: PolicyStore, inline_jobs, chat_graph
) -> dict:
    headers = auth_header(interviewer_token)
    client.post(
        "/api/v1/policies",
        headers=headers,
        files={
            "file": (
                "h.txt",
                io.BytesIO(b"Annual leave of twenty days. We hire on merit."),
                "text/plain",
            )
        },
    )
    job = client.post(
        "/api/v1/jobs",
        headers=headers,
        json={
            "title": "Backend Engineer",
            "requirements": [{"text": "Python", "is_mandatory": True}, {"text": "PostgreSQL"}],
        },
    ).json()
    response = client.post(f"/api/v1/jobs/{job['id']}/description/generate", headers=headers)
    assert response.status_code == 202
    assert inline_jobs[-1]["error"] is None, inline_jobs[-1]["error"]
    return job


def _chat(client, token, job_id, text):
    return client.post(
        f"/api/v1/jobs/{job_id}/description/chat",
        json={"message": text},
        headers=auth_header(token),
    )


def test_generating_records_version_one_and_a_chat_entry(
    client: TestClient, interviewer_token: str, job_with_description
):
    headers = auth_header(interviewer_token)
    job_id = job_with_description["id"]
    versions = client.get(f"/api/v1/jobs/{job_id}/description/versions", headers=headers).json()
    assert [(v["version"], v["source"]) for v in versions] == [(1, "generated")]

    chat = client.get(f"/api/v1/jobs/{job_id}/description/chat", headers=headers).json()
    assert len(chat) == 1
    assert chat[0]["role"] == "assistant"
    assert chat[0]["version"] == 1


def test_a_chat_request_revises_the_description(
    client: TestClient, interviewer_token: str, job_with_description, inline_jobs
):
    headers = auth_header(interviewer_token)
    job_id = job_with_description["id"]

    response = _chat(client, interviewer_token, job_id, "make it more formal")
    assert response.status_code == 202, response.text
    assert response.json()["message"]["role"] == "user"
    assert inline_jobs[-1]["error"] is None, inline_jobs[-1]["error"]
    assert inline_jobs[-1]["result"]["changed"] is True

    job = client.get(f"/api/v1/jobs/{job_id}", headers=headers).json()
    assert "Revised: make it more formal" in job["description_md"]
    assert job["policy_citations"]

    chat = client.get(f"/api/v1/jobs/{job_id}/description/chat", headers=headers).json()
    assert [(m["role"], m["status"]) for m in chat[1:]] == [
        ("user", "done"),
        ("assistant", "done"),
    ]
    assert chat[2]["content"] == "Done as asked."
    assert chat[2]["version"] == 2

    versions = client.get(f"/api/v1/jobs/{job_id}/description/versions", headers=headers).json()
    assert [(v["version"], v["source"]) for v in versions] == [(2, "chat"), (1, "generated")]


def test_a_question_gets_an_answer_and_no_new_version(
    client: TestClient, interviewer_token: str, job_with_description
):
    headers = auth_header(interviewer_token)
    job_id = job_with_description["id"]
    before = client.get(f"/api/v1/jobs/{job_id}", headers=headers).json()["description_md"]

    _chat(client, interviewer_token, job_id, "where does the leave come from?")

    after = client.get(f"/api/v1/jobs/{job_id}", headers=headers).json()["description_md"]
    assert after == before
    chat = client.get(f"/api/v1/jobs/{job_id}/description/chat", headers=headers).json()
    assert chat[-1]["content"] == "It comes from the handbook."
    assert chat[-1]["version"] is None
    versions = client.get(f"/api/v1/jobs/{job_id}/description/versions", headers=headers).json()
    assert len(versions) == 1


def test_restore_undoes_a_change_and_can_itself_be_undone(
    client: TestClient, interviewer_token: str, job_with_description
):
    headers = auth_header(interviewer_token)
    job_id = job_with_description["id"]
    original = client.get(f"/api/v1/jobs/{job_id}", headers=headers).json()["description_md"]
    _chat(client, interviewer_token, job_id, "rewrite the summary")

    versions = client.get(f"/api/v1/jobs/{job_id}/description/versions", headers=headers).json()
    v1 = next(v for v in versions if v["version"] == 1)
    response = client.post(
        f"/api/v1/jobs/{job_id}/description/versions/{v1['id']}/restore", headers=headers
    )
    assert response.status_code == 200
    assert response.json()["description_md"] == original

    versions = client.get(f"/api/v1/jobs/{job_id}/description/versions", headers=headers).json()
    assert versions[0]["version"] == 3
    assert versions[0]["source"] == "restored"
    assert versions[0]["restored_from"] == 1


def test_restoring_another_jobs_version_is_refused(
    client: TestClient, interviewer_token: str, job_with_description
):
    headers = auth_header(interviewer_token)
    other = client.post(
        "/api/v1/jobs", headers=headers, json={"title": "Other", "requirements": [{"text": "Go"}]}
    ).json()
    versions = client.get(
        f"/api/v1/jobs/{job_with_description['id']}/description/versions", headers=headers
    ).json()
    response = client.post(
        f"/api/v1/jobs/{other['id']}/description/versions/{versions[0]['id']}/restore",
        headers=headers,
    )
    assert response.status_code == 404


def test_manual_save_adds_a_version_only_when_the_text_changes(
    client: TestClient, interviewer_token: str, job_with_description
):
    headers = auth_header(interviewer_token)
    job_id = job_with_description["id"]
    current = client.get(f"/api/v1/jobs/{job_id}", headers=headers).json()["description_md"]

    client.put(
        f"/api/v1/jobs/{job_id}/description", json={"description_md": current}, headers=headers
    )
    client.put(
        f"/api/v1/jobs/{job_id}/description",
        json={"description_md": current + "\n## How to apply\n\nThrough the job page.\n"},
        headers=headers,
    )
    versions = client.get(f"/api/v1/jobs/{job_id}/description/versions", headers=headers).json()
    assert [(v["version"], v["source"]) for v in versions] == [(2, "manual"), (1, "generated")]


def test_a_failed_request_is_reported_in_the_chat(
    client: TestClient, interviewer_token: str, job_with_description, inline_jobs
):
    headers = auth_header(interviewer_token)
    job_id = job_with_description["id"]
    before = client.get(f"/api/v1/jobs/{job_id}", headers=headers).json()["description_md"]

    response = _chat(client, interviewer_token, job_id, "please fail now")
    assert response.status_code == 202
    assert inline_jobs[-1]["error"] is not None

    chat = client.get(f"/api/v1/jobs/{job_id}/description/chat", headers=headers).json()
    assert chat[-2]["status"] == "failed"
    assert chat[-1]["role"] == "assistant"
    assert chat[-1]["status"] == "failed"
    assert "busy or unavailable" in chat[-1]["content"]
    after = client.get(f"/api/v1/jobs/{job_id}", headers=headers).json()["description_md"]
    assert after == before

    # the chat isn't stuck afterwards
    assert _chat(client, interviewer_token, job_id, "make it shorter").status_code == 202


def test_only_one_request_at_a_time(
    client: TestClient, interviewer_token: str, job_with_description, db_session
):
    job_id = job_with_description["id"]
    db_session.add(
        JobDescriptionMessage(
            job_id=job_id, role="user", content="earlier", status=ChatMessageStatus.PENDING
        )
    )
    db_session.commit()

    response = _chat(client, interviewer_token, job_id, "another one")
    assert response.status_code == 409
    assert "still working" in response.json()["error"]["message"]


def test_a_stale_pending_message_does_not_block_the_chat(
    client: TestClient, interviewer_token: str, job_with_description, db_session
):
    job_id = job_with_description["id"]
    stuck = JobDescriptionMessage(
        job_id=job_id, role="user", content="earlier", status=ChatMessageStatus.PENDING
    )
    db_session.add(stuck)
    db_session.commit()
    stuck.created_at = datetime.now(UTC) - timedelta(hours=1)
    db_session.commit()

    assert _chat(client, interviewer_token, job_id, "another one").status_code == 202
    db_session.refresh(stuck)
    assert stuck.status == ChatMessageStatus.FAILED


def test_chat_message_length_is_validated(
    client: TestClient, interviewer_token: str, job_with_description
):
    response = _chat(client, interviewer_token, job_with_description["id"], "x" * 2001)
    assert response.status_code == 422


def test_candidates_cannot_chat_or_see_versions(
    client: TestClient, candidate_token: str, job_with_description
):
    job_id = job_with_description["id"]
    headers = auth_header(candidate_token)
    assert _chat(client, candidate_token, job_id, "hello there").status_code == 403
    assert client.get(f"/api/v1/jobs/{job_id}/description/chat", headers=headers).status_code == 403
    response = client.get(f"/api/v1/jobs/{job_id}/description/versions", headers=headers)
    assert response.status_code == 403


def test_an_identical_rewrite_does_not_add_a_version(
    client: TestClient, interviewer_token: str, job_with_description
):
    headers = auth_header(interviewer_token)
    job_id = job_with_description["id"]
    # the fake writes "Revised: <request>", so the same request twice gives the same text
    _chat(client, interviewer_token, job_id, "tidy it up")
    _chat(client, interviewer_token, job_id, "tidy it up")
    versions = client.get(f"/api/v1/jobs/{job_id}/description/versions", headers=headers).json()
    assert [v["version"] for v in versions] == [2, 1]
    chat = client.get(f"/api/v1/jobs/{job_id}/description/chat", headers=headers).json()
    assert chat[-1]["version"] is None
