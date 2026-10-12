import io
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import select

from app.agents.jd.graph import MAX_ATTEMPTS, build_jd_graph
from app.agents.jd.review import review_draft
from app.errors import ExternalServiceError
from app.models.analysis import AgentRun
from app.rag.policy_store import PolicyChunk, PolicyStore
from tests.conftest import auth_header

PROFILE = {
    "title": "Backend Engineer",
    "department": "Engineering",
    "location": "Dhaka",
    "employment_type": "Full-time",
    "min_years_experience": 3,
    "selection_criteria": None,
    "requirements": [
        {
            "id": 1,
            "text": "Python web development",
            "category": "technical_skill",
            "is_mandatory": True,
        },
        {"id": 2, "text": "PostgreSQL", "category": "technical_skill", "is_mandatory": False},
    ],
}

CHUNKS = [
    PolicyChunk("policy-1-0", 1, "Handbook", 0, "We offer 20 days of annual leave.", 0.7),
    PolicyChunk("policy-1-1", 1, "Handbook", 1, "We hire on merit only.", 0.6),
]


def good_draft(profile, chunks, feedback) -> dict:
    ids = [c["chunk_id"] for c in chunks]
    return {
        "summary": "Build and run the order-routing services.",
        "responsibilities": ["Design APIs", "Review code"],
        "required": [{"text": "Python web development", "requirement_ids": [1]}],
        "nice_to_have": [{"text": "PostgreSQL", "requirement_ids": [2]}],
        "what_we_offer": [{"text": "20 days of annual leave", "source_ids": ids[:1]}]
        if ids
        else [],
        "about_company": {"text": "A data company.", "source_ids": ids[:1]},
        "equal_opportunity": {"text": "We hire on merit.", "source_ids": ids[-1:]},
    }


def bad_draft(profile, chunks, feedback) -> dict:
    draft = good_draft(profile, chunks, feedback)
    draft["required"] = []
    draft["summary"] = "Join our young and energetic team."
    draft["what_we_offer"] = [
        {"text": "Free lunch", "source_ids": []},
        {"text": "Annual leave", "source_ids": ["policy-9-9"]},
    ]
    return draft


def legacy(fn):
    # adapts the (profile, chunks, feedback) fakes to the drafter interface
    def drafter(data):
        return {"draft": fn(data["profile"], data["chunks"], data["feedback"]), "reply": ""}

    return drafter


def retriever_returning(chunks):
    return lambda queries: chunks


def run(graph, profile=PROFILE) -> dict:
    return graph.invoke(
        {"job_id": 1, "thread_id": "t", "profile": profile, "errors": []},
        config={"configurable": {"thread_id": "t"}},
    )


def test_clean_draft_finishes_in_one_attempt():
    graph = build_jd_graph(drafter=legacy(good_draft), retriever=retriever_returning(CHUNKS))
    final = run(graph)

    assert final["attempts"] == 1
    assert final["feedback"] == []
    assert final["warnings"] == []
    md = final["description_md"]
    assert md.startswith("# Backend Engineer")
    for heading in (
        "## About the role",
        "## What you'll need",
        "## Nice to have",
        "## What we offer",
    ):
        assert heading in md
    cited = {c["chunk_id"]: c["used_for"] for c in final["citations"]}
    assert cited == {
        "policy-1-0": ["What we offer", "About us"],
        "policy-1-1": ["Equal opportunity"],
    }


def test_review_sends_problems_back_and_the_redraft_fixes_them():
    seen_feedback: list[list[str]] = []

    def drafter(profile, chunks, feedback):
        seen_feedback.append(list(feedback))
        return (
            bad_draft(profile, chunks, feedback)
            if not feedback
            else good_draft(profile, chunks, feedback)
        )

    final = run(build_jd_graph(drafter=legacy(drafter), retriever=retriever_returning(CHUNKS)))

    assert final["attempts"] == 2
    assert seen_feedback[0] == []
    problems = " ".join(seen_feedback[1])
    assert "Must-have requirement 1" in problems
    assert '"young"' in problems and '"energetic"' in problems
    assert "Free lunch" in problems
    assert "policy-9-9" in problems
    assert final["warnings"] == []


def test_stops_after_the_attempt_limit_and_reports_what_is_left():
    calls = []

    def drafter(profile, chunks, feedback):
        calls.append(1)
        return bad_draft(profile, chunks, feedback)

    final = run(build_jd_graph(drafter=legacy(drafter), retriever=retriever_returning(CHUNKS)))

    assert len(calls) == MAX_ATTEMPTS
    assert final["description_md"]
    assert any(w.startswith("Not fixed automatically") for w in final["warnings"])
    # the invented source never reaches the citations
    assert all(c["chunk_id"] != "policy-9-9" for c in final["citations"])


def test_retrieval_failure_degrades_to_a_draft_without_policy():
    def broken(queries):
        raise ExternalServiceError("The policy search service is not reachable.")

    final = run(build_jd_graph(drafter=legacy(good_draft), retriever=broken))

    assert final["description_md"]
    assert final["citations"] == []
    assert final["errors"][0]["code"] == "retrieval_failed"
    assert "could not be searched" in final["warnings"][0]


def test_llm_failure_on_the_first_attempt_gives_no_description():
    def failing(profile, chunks, feedback):
        raise ExternalServiceError("The AI service could not complete the request.")

    final = run(build_jd_graph(drafter=legacy(failing), retriever=retriever_returning(CHUNKS)))
    assert final["description_md"] == ""
    assert final["errors"][-1]["code"] == "llm_failed"


def test_llm_failure_on_a_retry_keeps_the_last_checked_draft():
    def drafter(profile, chunks, feedback):
        if feedback:
            raise ExternalServiceError("The AI service could not complete the request.")
        return bad_draft(profile, chunks, feedback)

    final = run(build_jd_graph(drafter=legacy(drafter), retriever=retriever_returning(CHUNKS)))
    assert final["attempts"] == 2
    assert final["description_md"]
    assert any("last checked draft" in w for w in final["warnings"])


def test_queries_cover_the_role_and_the_standard_topics():
    captured = []

    def retriever(queries):
        captured.extend(queries)
        return []

    run(build_jd_graph(drafter=legacy(good_draft), retriever=retriever))
    joined = " | ".join(captured)
    assert "Backend Engineer Engineering role and team" in joined
    assert "equal opportunity" in joined
    assert "Python web development" in joined
    assert "Dhaka" in joined


def test_checkpoints_record_every_step():
    saver = InMemorySaver()

    def drafter(profile, chunks, feedback):
        return (
            bad_draft(profile, chunks, feedback)
            if not feedback
            else good_draft(profile, chunks, feedback)
        )

    graph = build_jd_graph(
        checkpointer=saver, drafter=legacy(drafter), retriever=retriever_returning(CHUNKS)
    )
    run(graph)

    history = list(graph.get_state_history({"configurable": {"thread_id": "t"}}))
    # each checkpoint records the node that runs next
    nodes = [s.next[0] for s in reversed(history) if s.next and s.next[0] != "__start__"]
    assert nodes == [
        "plan_queries",
        "retrieve_policy",
        "draft",
        "review",
        "draft",
        "review",
        "finalise",
    ]


@pytest.mark.parametrize(
    "text, term",
    [
        ("Applicants aged under 30 preferred", "aged under 30"),
        ("A rockstar developer", "rockstar"),
        ("He will lead the team", "he"),
        ("Native English speaker", "native english"),
    ],
)
def test_review_flags_biased_wording(text, term):
    draft = good_draft(PROFILE, [{"chunk_id": "policy-1-0"}, {"chunk_id": "policy-1-1"}], [])
    draft["summary"] = text
    _, problems = review_draft(draft, PROFILE, {"policy-1-0", "policy-1-1"})
    assert any(f'"{term}"' in p for p in problems)


def test_review_accepts_neutral_wording():
    draft = good_draft(PROFILE, [{"chunk_id": "policy-1-0"}, {"chunk_id": "policy-1-1"}], [])
    _, problems = review_draft(draft, PROFILE, {"policy-1-0", "policy-1-1"})
    assert problems == []


# --- end to end through the api ------------------------------------------


@pytest.fixture
def fake_jd_graph(monkeypatch: pytest.MonkeyPatch):
    @contextmanager
    def graph():
        yield build_jd_graph(drafter=legacy(good_draft))

    monkeypatch.setattr("app.worker.tasks._jd_graph", graph)


def test_generate_through_the_api(
    client: TestClient,
    interviewer_token: str,
    policy_store: PolicyStore,
    inline_jobs,
    fake_jd_graph,
    db_session,
):
    headers = auth_header(interviewer_token)
    client.post(
        "/api/v1/policies",
        headers=headers,
        files={
            "file": (
                "h.txt",
                io.BytesIO(
                    b"Benefits: annual leave of twenty days and health insurance.\n\n"
                    b"Equal opportunity: we hire on merit and never on age or gender."
                ),
                "text/plain",
            )
        },
    )
    job = client.post(
        "/api/v1/jobs",
        headers=headers,
        json={
            "title": "Backend Engineer",
            "requirements": [
                {"text": "Python web development", "is_mandatory": True},
                {"text": "PostgreSQL"},
            ],
        },
    ).json()
    # the fake drafter cites requirement ids 1 and 2, which match a fresh database
    assert [r["id"] for r in job["requirements"]] == [1, 2]

    response = client.post(f"/api/v1/jobs/{job['id']}/description/generate", headers=headers)
    assert response.status_code == 202
    assert inline_jobs[-1]["error"] is None, inline_jobs[-1]["error"]
    assert inline_jobs[-1]["result"]["attempts"] == 1

    updated = client.get(f"/api/v1/jobs/{job['id']}", headers=headers).json()
    assert updated["description_md"].startswith("# Backend Engineer")
    assert updated["policy_citations"]
    assert all(c["policy_title"] == "h" for c in updated["policy_citations"])

    run = db_session.scalar(select(AgentRun).where(AgentRun.graph_name == "job_description"))
    assert run is not None and run.status.value == "succeeded"
    assert run.context["job_id"] == job["id"]


def test_candidates_cannot_generate(
    client: TestClient, interviewer_token: str, candidate_token: str
):
    job = client.post(
        "/api/v1/jobs",
        headers=auth_header(interviewer_token),
        json={"title": "Backend Engineer", "requirements": [{"text": "Python"}]},
    ).json()
    response = client.post(
        f"/api/v1/jobs/{job['id']}/description/generate", headers=auth_header(candidate_token)
    )
    assert response.status_code == 403


def test_progress_messages_describe_the_step_that_runs_next():
    from app.services.job_descriptions import next_step_message

    assert next_step_message("retrieve_policy", {"policy_chunks": [1, 2]}) == (
        "Drafting the description from 2 policy passages."
    )
    assert next_step_message("review", {"feedback": ["a", "b"]}) == "Redrafting to fix 2 problems."
    assert next_step_message("review", {"feedback": []}) == "Putting the description together."
    assert next_step_message("finalise", {}) is None


def test_a_run_without_a_description_is_audited_as_failed(db_session):
    from app.models.job import Job, JobRequirement
    from app.services.job_descriptions import generate_description

    def failing(profile, chunks, feedback):
        raise ExternalServiceError("The AI service could not complete the request.")

    job = Job(title="Backend Engineer", requirements=[JobRequirement(text="Python")])
    db_session.add(job)
    db_session.commit()

    graph = build_jd_graph(drafter=legacy(failing), retriever=retriever_returning([]))
    with pytest.raises(ExternalServiceError, match="could not complete"):
        generate_description(db_session, job.id, graph)

    run = db_session.scalar(select(AgentRun))
    assert run.status.value == "failed"
    assert run.error_code == "no_output"
    assert db_session.get(Job, job.id).description_md is None


def test_a_fresh_draft_may_not_hide_facts_in_extra_sections():
    def drafter(profile, chunks, feedback):
        draft = good_draft(profile, chunks, feedback)
        if not feedback:
            draft["additional_sections"] = [
                {"heading": "Working environment", "body": "40 hours a week."}
            ]
        return draft

    seen = []

    def wrapped(data):
        seen.append(list(data["feedback"]))
        return {"draft": drafter(data["profile"], data["chunks"], data["feedback"]), "reply": ""}

    final = run(build_jd_graph(drafter=wrapped, retriever=retriever_returning(CHUNKS)))
    assert final["attempts"] == 2
    assert any("additional_sections" in p for p in seen[1])
    assert "Working environment" not in final["description_md"]
