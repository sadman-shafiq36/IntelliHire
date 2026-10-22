import json
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.agents.copilot import checks
from app.agents.copilot.graph import build_copilot_graph
from app.errors import ExternalServiceError
from app.models.analysis import AgentRun
from app.models.candidate import Candidate, CvDocument, CvExtraction
from app.models.enums import (
    InterviewStatus,
    RequirementCategory,
    RequirementMatchStatus,
    ScreeningStatus,
    SpeakerRole,
)
from app.models.interview import Interview, TranscriptTurn
from app.models.job import Job, JobRequirement
from app.models.screening import Screening, ScreeningRequirementResult
from app.rag.policy_store import PolicyChunk
from app.services import copilot_chat
from app.services.interviews import hash_token
from tests.conftest import auth_header

CV = (
    "Backend engineer. Built REST services in Django for five years. "
    "Deployed containers with Docker on AWS."
)
KEYWORDS = ["django", "kubernetes", "mentor"]


def keyword_embedder(texts: list[str]) -> list[list[float]]:
    # one dimension per topic, so "discussed" is exact in tests
    out = []
    for text in texts:
        low = text.lower()
        out.append([1.0 if k in low else 0.0 for k in KEYWORDS] + [0.1])
    return out


def retriever(queries: list[str]) -> list[PolicyChunk]:
    return [
        PolicyChunk(
            chunk_id="policy-9-0",
            policy_id=9,
            policy_title="Kubernetes guide",
            chunk_index=0,
            text="Liveness probes restart a stuck container; readiness probes gate traffic.",
            score=0.9,
        )
    ]


def make_suggester(*answers: list[dict]):
    calls: list[dict] = []

    def suggester(payload: dict):
        calls.append(payload)
        return {"suggestions": answers[min(len(calls), len(answers)) - 1]}, "fake-model"

    suggester.calls = calls  # type: ignore[attr-defined]
    return suggester


def good(req_ids: dict[str, int]) -> list[dict]:
    return [
        {
            "requirement_id": req_ids["k8s"],
            "question": "How would you decide between a liveness and a readiness probe?",
            "why": "Screening found no Kubernetes experience in the CV.",
            "evidence": [
                {"source": "cv", "quote": "Deployed containers with Docker", "turn_id": None}
            ],
            "answer_points": [
                {"point": "Liveness restarts a stuck container.", "passage_ids": ["policy-9-0"]},
                {"point": "Made-up point.", "passage_ids": ["policy-404-0"]},
            ],
        },
        {
            "requirement_id": req_ids["mentor"],
            "question": "Tell me about a time you helped a less experienced engineer improve.",
            "why": "Mentoring was only partly shown in the CV.",
            "evidence": [{"source": "cv", "quote": "led a team of fifty", "turn_id": None}],
            "answer_points": [],
        },
        {
            "requirement_id": req_ids["django"],
            "question": "What would you change in the Django payments service you described?",
            "why": "Builds on the candidate's own example.",
            "evidence": [
                {"source": "transcript", "quote": "REST services in Django", "turn_id": 2},
            ],
            "answer_points": [],
        },
    ]


def state(**overrides) -> dict:
    base = {
        "thread_id": "copilot-test",
        "interview_id": 1,
        "job": {"title": "Backend Engineer"},
        "requirements": [
            {"id": 1, "text": "Django web development", "category": "technical_skill",
             "is_mandatory": True},
            {"id": 2, "text": "Kubernetes in production", "category": "technical_skill",
             "is_mandatory": False},
            {"id": 3, "text": "Mentor junior engineers", "category": "experience",
             "is_mandatory": False},
        ],
        "screening": {
            "1": {"status": "met", "evidence_quote": "Built REST services in Django"},
            "2": {"status": "unmet", "evidence_quote": None},
            "3": {"status": "partially_met", "evidence_quote": None},
        },
        "dimension_scores": {"technical_skills": 62},
        "cv_text": CV,
        "turns": [
            {"id": 1, "speaker_role": "interviewer", "text": "Tell me about your Django work."},
            {"id": 2, "speaker_role": "candidate",
             "text": "I built REST services in Django for a payments team."},
        ],
        "already_asked": [],
        "focus": None,
        "errors": [],
    }  # fmt: skip
    base.update(overrides)
    return base


IDS = {"django": 1, "k8s": 2, "mentor": 3}


def run(suggester, **overrides):
    graph = build_copilot_graph(
        suggester=suggester,
        embedder=keyword_embedder,
        retriever=retriever,
        discussed_threshold=0.5,
    )
    return graph.invoke(state(**overrides))


# --- graph ---------------------------------------------------------------------------


def test_plan_puts_undiscussed_gaps_first():
    final = run(make_suggester(good(IDS)))
    order = [t["requirement_id"] for t in final["targets"]]
    # kubernetes: unmet and not discussed; mentoring: partly met; django: met and discussed
    assert order == [2, 3, 1]
    django = final["targets"][2]
    assert django["discussed"] is True and django["discussed_turn_id"] == 1
    assert final["targets"][0]["discussed"] is False


def test_suggestions_keep_only_traceable_evidence_and_cited_points():
    final = run(make_suggester(good(IDS)))
    assert final["outcome"] == "suggested"
    by_req = {s["requirement_id"]: s for s in final["suggestions"]}
    k8s = by_req[2]
    assert [p["passage_ids"] for p in k8s["answer_points"]] == [["policy-9-0"]]
    assert k8s["evidence"][0]["quote"] == "Deployed containers with Docker"
    # a cv quote that isn't in the cv is dropped
    assert by_req[3]["evidence"] == []
    # a transcript quote is checked against that line
    assert by_req[1]["evidence"][0]["turn_id"] == 2


def test_rejected_questions_go_back_once_with_feedback():
    bad = [
        {"requirement_id": 99, "question": "Anything?", "why": "", "evidence": [],
         "answer_points": []},
        {"requirement_id": 2, "question": "Are you married, and do you have kids?", "why": "",
         "evidence": [], "answer_points": []},
        good(IDS)[0],
    ]  # fmt: skip
    suggester = make_suggester(bad, good(IDS))
    final = run(suggester)
    assert len(suggester.calls) == 2
    feedback = " ".join(suggester.calls[1]["feedback"])
    assert "not one of the requirements" in feedback
    assert "personal characteristics" in feedback
    # the one that passed the first time is kept, the repeat of it is not added twice
    questions = [s["question"] for s in final["suggestions"]]
    assert len(questions) == 3 and len(set(questions)) == 3
    assert not any("married" in q for q in questions)


def test_a_question_already_suggested_is_not_suggested_again():
    asked = [good(IDS)[0]["question"]]
    suggester = make_suggester(good(IDS))
    final = run(suggester, already_asked=asked)
    assert asked[0] not in [s["question"] for s in final["suggestions"]]
    assert suggester.calls[0]["already_asked"] == asked


def test_no_reference_passages_means_no_answer_points():
    graph = build_copilot_graph(
        suggester=make_suggester(good(IDS)),
        embedder=keyword_embedder,
        retriever=lambda q: [],
        discussed_threshold=0.5,
    )
    final = graph.invoke(state())
    assert final["outcome"] == "suggested"
    assert all(s["answer_points"] == [] for s in final["suggestions"])


def test_the_llm_failing_fails_the_run():
    def broken(payload):
        raise ExternalServiceError("The AI service is busy.")

    final = run(broken)
    assert final["outcome"] == "failed"
    assert "busy" in final["reason"]


def test_a_job_without_requirements_has_nothing_to_suggest():
    suggester = make_suggester(good(IDS))
    final = run(suggester, requirements=[])
    assert final["outcome"] == "nothing"
    assert suggester.calls == []


def test_an_embedding_failure_still_suggests():
    def broken(texts):
        raise ExternalServiceError("down")

    graph = build_copilot_graph(
        suggester=make_suggester(good(IDS)), embedder=broken, retriever=retriever
    )
    final = graph.invoke(state())
    assert final["outcome"] == "suggested"
    assert all(t["discussed"] is None for t in final["targets"])
    assert final["warnings"]


def test_protected_topics_are_caught_but_technical_words_are_not():
    assert checks.protected_topics("Are you married?") == ["married"]
    assert checks.protected_topics("Where are you from originally?") == ["where are you from"]
    for fine in [
        "How do you find a race condition?",
        "How would you walk the child nodes of a tree?",
        "How does the CORS origin check work?",
        "What does Cache-Control max-age do?",
    ]:
        assert checks.protected_topics(fine) == [], fine


# --- api -----------------------------------------------------------------------------


@pytest.fixture
def interview(db_session):
    job = Job(title="Backend Engineer")
    db_session.add(job)
    db_session.flush()
    reqs = {
        "django": JobRequirement(job_id=job.id, text="Django web development",
                                 category=RequirementCategory.TECHNICAL_SKILL, is_mandatory=True),
        "k8s": JobRequirement(job_id=job.id, text="Kubernetes in production",
                              category=RequirementCategory.TECHNICAL_SKILL),
        "mentor": JobRequirement(job_id=job.id, text="Mentor junior engineers",
                                 category=RequirementCategory.EXPERIENCE),
    }  # fmt: skip
    candidate = Candidate(full_name="Ada Lovelace", email="ada@example.com")
    db_session.add_all([*reqs.values(), candidate])
    db_session.flush()
    doc = CvDocument(
        candidate_id=candidate.id,
        job_id=job.id,
        original_filename="cv.pdf",
        storage_path="/tmp/cv.pdf",
        content_type="application/pdf",
        size_bytes=1,
    )
    db_session.add(doc)
    db_session.flush()
    db_session.add(CvExtraction(cv_document_id=doc.id, raw_text=CV))
    screening = Screening(
        job_id=job.id,
        candidate_id=candidate.id,
        cv_document_id=doc.id,
        status=ScreeningStatus.SCREENED,
    )
    db_session.add(screening)
    db_session.flush()
    for key, status in [
        ("django", RequirementMatchStatus.MET),
        ("k8s", RequirementMatchStatus.UNMET),
        ("mentor", RequirementMatchStatus.PARTIALLY_MET),
    ]:
        db_session.add(
            ScreeningRequirementResult(
                screening_id=screening.id, job_requirement_id=reqs[key].id, status=status
            )
        )
    start = datetime.now(UTC)
    row = Interview(
        job_id=job.id,
        candidate_id=candidate.id,
        status=InterviewStatus.IN_PROGRESS,
        scheduled_start=start,
        scheduled_end=start + timedelta(minutes=45),
        started_at=start,
        join_token=hash_token("candidate-link"),
    )
    db_session.add(row)
    db_session.flush()
    for seq, (role, text) in enumerate(
        [
            (SpeakerRole.INTERVIEWER, "Tell me about your Django work."),
            (SpeakerRole.CANDIDATE, "I built REST services in Django for a payments team."),
        ],
        start=1,
    ):
        db_session.add(
            TranscriptTurn(
                interview_id=row.id, sequence=seq, speaker_role=role, text=text,
                start_offset_ms=seq * 1000, end_offset_ms=seq * 1000 + 500,
            )
        )  # fmt: skip
    db_session.commit()
    return row, {k: r.id for k, r in reqs.items()}


@pytest.fixture
def copilot_graph(monkeypatch: pytest.MonkeyPatch, db_session):
    holder: dict = {}

    @contextmanager
    def fake():
        yield build_copilot_graph(
            suggester=holder["suggester"],
            embedder=keyword_embedder,
            retriever=retriever,
            discussed_threshold=0.5,
        )

    monkeypatch.setattr("app.worker.tasks._copilot_graph", fake)
    return holder


def test_suggestions_are_saved_and_listed_with_their_basis(
    client: TestClient, interviewer_token: str, interview, copilot_graph, inline_jobs, db_session
):
    row, ids = interview
    turn_ids = [
        t.id for t in db_session.scalars(select(TranscriptTurn).order_by(TranscriptTurn.id))
    ]
    answers = good(ids)
    answers[2]["evidence"][0]["turn_id"] = turn_ids[1]
    copilot_graph["suggester"] = make_suggester(answers)

    response = client.post(
        f"/api/v1/interviews/{row.id}/copilot/suggestions",
        json={"focus": "  cloud   deployment "},
        headers=auth_header(interviewer_token),
    )
    assert response.status_code == 202, response.text
    assert inline_jobs[0]["error"] is None
    assert copilot_graph["suggester"].calls[0]["focus"] == "cloud deployment"

    listed = client.get(
        f"/api/v1/interviews/{row.id}/copilot/suggestions", headers=auth_header(interviewer_token)
    ).json()
    assert len(listed) == 3 and {s["batch"] for s in listed} == {1}
    k8s = next(s for s in listed if s["requirement"]["id"] == ids["k8s"])
    assert k8s["basis"]["screening"] == "unmet" and k8s["basis"]["discussed"] is False
    assert k8s["sources"][0]["title"] == "Kubernetes guide"
    assert k8s["answer_points"][0]["passage_ids"] == ["policy-9-0"]
    django = next(s for s in listed if s["requirement"]["id"] == ids["django"])
    assert django["evidence"][0]["turn_id"] == turn_ids[1]

    # the audit row lets candidate deletion find this thread's checkpoints
    run_row = db_session.scalar(select(AgentRun).where(AgentRun.graph_name == "interview_copilot"))
    assert run_row.context["candidate_id"] == row.candidate_id

    # a second batch is numbered on and doesn't repeat the first
    client.post(
        f"/api/v1/interviews/{row.id}/copilot/suggestions",
        json={},
        headers=auth_header(interviewer_token),
    )
    second = copilot_graph["suggester"].calls[-1]
    assert len(second["already_asked"]) == 3

    used = client.patch(
        f"/api/v1/interviews/{row.id}/copilot/suggestions/{k8s['id']}",
        json={"was_used": True},
        headers=auth_header(interviewer_token),
    )
    assert used.json() == {"id": k8s["id"], "was_used": True}


def test_copilot_is_for_interviewers_only(
    client: TestClient, candidate_token: str, interview, copilot_graph, inline_jobs
):
    row, _ = interview
    base = f"/api/v1/interviews/{row.id}/copilot/suggestions"
    headers = auth_header(candidate_token)
    assert client.post(base, json={}, headers=headers).status_code == 403
    assert client.get(base, headers=headers).status_code == 403
    assert client.patch(f"{base}/1", json={"was_used": True}, headers=headers).status_code == 403
    assert inline_jobs == []


def test_no_suggestions_after_the_interview_ended(
    client: TestClient, interviewer_token: str, interview, copilot_graph, inline_jobs, db_session
):
    row, _ = interview
    row.status = InterviewStatus.COMPLETED
    db_session.commit()
    response = client.post(
        f"/api/v1/interviews/{row.id}/copilot/suggestions",
        json={},
        headers=auth_header(interviewer_token),
    )
    assert response.status_code >= 400
    assert "ended" in response.json()["error"]["message"]
    assert inline_jobs == []


# --- chat ----------------------------------------------------------------------------


def _events(body: str) -> list[tuple[str, dict]]:
    out = []
    for block in body.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        out.append((lines["event"], json.loads(lines["data"])))
    return out


@pytest.fixture
def chat_streamer(monkeypatch: pytest.MonkeyPatch, db_session):
    from tests.conftest import _NonClosingSession

    holder: dict = {"pieces": [], "prompts": []}

    def streamer(prompt: str, system: str = "", quick: bool = False):
        holder["prompts"].append(prompt)
        if holder.get("fail"):
            raise ExternalServiceError("The AI service is busy.")
        for piece in holder["pieces"]:
            yield "fake-model", piece

    monkeypatch.setattr("app.api.v1.copilot.chat_streamer", lambda: streamer)
    monkeypatch.setattr(
        "app.api.v1.copilot.get_session_factory",
        lambda: lambda: _NonClosingSession(db_session),
    )
    return holder


def test_check_reply_keeps_only_real_citations():
    passages = {"S1": {"chunk_id": "policy-9-0", "title": "Guide", "text": "Probes restart."}}
    turns = {7: {"id": 7, "speaker_role": "candidate", "text": "I used Django."}}
    text, sources = copilot_chat.check_reply(
        'Probes restart pods [S1] [S4]. They said so [T7] [T99]. The CV says "REST services in '
        'Django" [CV] and "ten years of Rust" [CV].',
        passages,
        turns,
        CV,
    )
    assert "[S4]" not in text and "[T99]" not in text
    assert "[S1]" in text and "[T7]" in text
    assert '"ten years of Rust" [not found in the CV]' in text
    assert [s["label"] for s in sources] == ["S1", "T7", "CV"]
    assert sources[2]["quote"] == "REST services in Django"


def test_chat_streams_and_saves_the_checked_reply(
    client: TestClient,
    interviewer_token: str,
    interview,
    chat_streamer,
    reference_store,
    inline_jobs,
    db_session,
):
    row, _ = interview
    client.post(
        "/api/v1/references",
        headers=auth_header(interviewer_token),
        files={"file": ("k8s.txt", b"Liveness probes restart a stuck container.", "text/plain")},
    )
    turn = db_session.scalars(select(TranscriptTurn).order_by(TranscriptTurn.id)).all()[1]
    chat_streamer["pieces"] = [
        "A strong answer says liveness probes restart a stuck container [S1] [S7]. ",
        f"They already mentioned Django [T{turn.id}].",
    ]
    response = client.post(
        f"/api/v1/interviews/{row.id}/copilot/chat",
        json={"message": "What should a good answer about liveness probes include?"},
        headers=auth_header(interviewer_token),
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    events = _events(response.text)
    assert [e[0] for e in events] == ["delta", "delta", "done"]
    done = events[-1][1]
    assert "[S7]" not in done["content"] and "[S1]" in done["content"]
    assert {s["label"] for s in done["sources"]} == {"S1", f"T{turn.id}"}
    # the prompt carries the passage, the transcript line and the redacted cv
    prompt = chat_streamer["prompts"][0]
    assert "[S1] (k8s)" in prompt and f"[T{turn.id}] candidate:" in prompt and CV in prompt

    listed = client.get(
        f"/api/v1/interviews/{row.id}/copilot/chat", headers=auth_header(interviewer_token)
    ).json()
    assert [m["role"] for m in listed] == ["user", "assistant"]
    assert listed[1]["content"] == done["content"]


def test_a_failed_chat_reply_is_recorded_as_failed(
    client: TestClient, interviewer_token: str, interview, chat_streamer, reference_store
):
    row, _ = interview
    chat_streamer["fail"] = True
    response = client.post(
        f"/api/v1/interviews/{row.id}/copilot/chat",
        json={"message": "Summarise the interview so far."},
        headers=auth_header(interviewer_token),
    )
    events = _events(response.text)
    assert events[-1][0] == "error" and "busy" in events[-1][1]["message"]
    listed = client.get(
        f"/api/v1/interviews/{row.id}/copilot/chat", headers=auth_header(interviewer_token)
    ).json()
    assert listed[-1]["status"] == "failed"


def test_chat_is_for_interviewers_only(
    client: TestClient, candidate_token: str, interview, chat_streamer
):
    row, _ = interview
    path = f"/api/v1/interviews/{row.id}/copilot/chat"
    headers = auth_header(candidate_token)
    assert client.post(path, json={"message": "hi"}, headers=headers).status_code == 403
    assert client.get(path, headers=headers).status_code == 403
    assert chat_streamer["prompts"] == []


def test_a_reply_cut_off_by_the_browser_is_recorded(db_session, interview, reference_store):
    from tests.conftest import _NonClosingSession

    row, _ = interview
    prepared = copilot_chat.prepare(db_session, row.id, "Summarise the interview so far.")

    def streamer(prompt, system="", quick=False):
        yield "fake-model", "They talked "
        yield "fake-model", "about Django."

    events = copilot_chat.stream(prepared, streamer, lambda: _NonClosingSession(db_session))
    assert next(events).startswith("event: delta")
    events.close()
    rows = copilot_chat.messages(db_session, row.id)
    assert [(m["role"], m["status"]) for m in rows] == [("user", "done"), ("assistant", "failed")]
