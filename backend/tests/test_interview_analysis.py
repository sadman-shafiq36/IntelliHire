from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.agents.analysis import checks
from app.agents.analysis.graph import build_analysis_graph
from app.errors import ExternalServiceError
from app.models.analysis import AgentRun, InterviewAnalysis
from app.models.candidate import Candidate
from app.models.enums import InterviewStatus, RequirementCategory, SpeakerRole
from app.models.interview import Interview, TranscriptTurn
from app.models.job import Job, JobRequirement
from app.rag.policy_store import PolicyChunk
from app.services import interview_sessions
from app.services.interviews import hash_token
from tests.conftest import auth_header

LINES = [
    ("interviewer", "Hi, thanks for joining. Can you hear me okay?"),
    ("candidate", "Yes, loud and clear."),
    ("interviewer", "What did you build with Django, and how did you test it?"),
    ("candidate", "I built REST services for payments in Django."),
    ("candidate", "We tested them with pytest and factory fixtures."),
    ("interviewer", "Why pytest over unittest?"),
    ("candidate", "Fixtures and plain asserts make the tests shorter."),
    ("interviewer", "Thanks, that's all from me."),
]
REQUIREMENTS = [
    {
        "id": 1,
        "text": "Django web development",
        "category": "technical_skill",
        "is_mandatory": True,
    },
    {
        "id": 2,
        "text": "Kubernetes in production",
        "category": "technical_skill",
        "is_mandatory": False,
    },
    {"id": 3, "text": "Automated testing", "category": "technical_skill", "is_mandatory": False},
]


def turns(ids: list[int] | None = None) -> list[dict]:
    ids = ids or list(range(1, len(LINES) + 1))
    return [{"id": i, "speaker_role": r, "text": t} for i, (r, t) in zip(ids, LINES, strict=True)]


def segmentation(t: list[dict]) -> dict:
    i = [x["id"] for x in t]
    return {
        "pairs": [
            {"question": "What did you build with Django", "question_turn_ids": [i[2]],
             "answer_turn_ids": [i[3]], "follow_up_of": None},
            {"question": "how did you test it?", "question_turn_ids": [i[2]],
             "answer_turn_ids": [i[4]], "follow_up_of": None},
            {"question": "Why pytest over unittest?", "question_turn_ids": [i[5]],
             "answer_turn_ids": [i[6]], "follow_up_of": 1},
        ],
        "small_talk_turn_ids": [i[0], i[1], i[7]],
    }  # fmt: skip


def evaluation(t: list[dict], req: dict[str, int] | None = None) -> dict:
    i = [x["id"] for x in t]
    req = req or {"django": 1, "k8s": 2, "testing": 3}
    return {
        "evaluations": [
            {"pair": 0, "relevance": 87, "accuracy": 130, "accuracy_basis": "general",
             "completeness": 70, "articulation": 80,
             "justification": f"Named a concrete Django service [T{i[3]}].",
             "requirements": [{"requirement_id": req["django"], "coverage": "covered",
                               "note": "payments REST services"}]},
            {"pair": 1, "relevance": 80, "accuracy": 75, "accuracy_basis": "reference",
             "completeness": 60, "articulation": 80,
             "justification": f"Mentions pytest fixtures [T{i[4]}] [S1] [S9].",
             "requirements": [{"requirement_id": req["testing"], "coverage": "partially_covered",
                               "note": "tools only"}]},
            {"pair": 2, "relevance": 90, "accuracy": None, "accuracy_basis": "not_applicable",
             "completeness": 80, "articulation": 85,
             "justification": f"Gave a reason [T{i[6]}].",
             "requirements": [{"requirement_id": req["testing"], "coverage": "covered",
                               "note": "reasoned choice"}]},
        ],
        "follow_ups": [
            {"requirement_id": req["k8s"], "question": "How have you run services on Kubernetes?"},
            {"requirement_id": req["django"], "question": "What about Django admin?"},
            {"requirement_id": req["k8s"], "question": "Are you married?"},
        ],
    }  # fmt: skip


def retriever(queries):
    return [
        PolicyChunk("policy-5-0", 5, "Testing guide", 0, "Fixtures share setup between tests.", 0.9)
    ]


def fake(*answers):
    calls: list[dict] = []

    def step(payload):
        calls.append(payload)
        answer = answers[min(len(calls), len(answers)) - 1]
        if isinstance(answer, Exception):
            raise answer
        return answer, "fake-model"

    step.calls = calls  # type: ignore[attr-defined]
    return step


def run(segmenter, evaluator, t=None, requirements=REQUIREMENTS):
    graph = build_analysis_graph(segmenter=segmenter, evaluator=evaluator, retriever=retriever)
    return graph.invoke(
        {
            "thread_id": "analysis-test",
            "interview_id": 1,
            "job": {"title": "Backend Engineer"},
            "requirements": requirements,
            "turns": t or turns(),
            "errors": [],
        }
    )


# --- graph ---------------------------------------------------------------------------


def test_pairs_scores_coverage_and_follow_ups():
    final = run(fake(segmentation(turns())), fake(evaluation(turns())))
    assert final["outcome"] == "analysed" and final["segmented_by"] == "ai"
    pairs = final["pairs"]
    # the two-part question became two pairs sharing the question line; small talk left out
    assert [p["question"] for p in pairs] == [
        "What did you build with Django",
        "how did you test it?",
        "Why pytest over unittest?",
    ]
    assert pairs[2]["follow_up_of"] == 1
    ev = final["evaluations"]
    # scores are clamped to 0-100 and snapped to 5s
    assert ev["0"]["relevance"] == 85 and ev["0"]["accuracy"] == 100
    # an invented passage citation is stripped, a real one kept
    assert "[S9]" not in ev["1"]["justification"] and "[S1]" in ev["1"]["justification"]
    assert ev["2"]["accuracy"] is None
    status = {c["requirement_id"]: c["status"] for c in final["coverage"]}
    assert status == {1: "covered", 2: "not_explored", 3: "covered"}
    # follow-ups only for what wasn't covered, never personal
    assert [f["question"] for f in final["follow_ups"]] == [
        "How have you run services on Kubernetes?"
    ]


def test_a_reworded_question_is_replaced_by_the_interviewers_words():
    seg = segmentation(turns())
    seg["pairs"][2]["question"] = "Could you explain your preference for pytest?"
    final = run(fake(seg), fake(evaluation(turns())))
    assert final["pairs"][2]["question"] == "Why pytest over unittest?"
    assert any("reworded" in w for w in final["warnings"])


def test_a_broken_split_falls_back_to_exchanges():
    bad = segmentation(turns())
    # an interviewer line given as the answer, twice
    bad["pairs"][0]["answer_turn_ids"] = [3]
    segmenter = fake(bad, bad)
    final = run(segmenter, fake(evaluation(turns())))
    assert len(segmenter.calls) == 2
    assert "interviewer" in " ".join(segmenter.calls[1]["feedback"]) or "candidate" in " ".join(
        segmenter.calls[1]["feedback"]
    )
    # with only some pairs passing, the ones that passed are used
    assert final["segmented_by"] == "ai"
    assert len(final["pairs"]) == 2


def test_no_usable_split_uses_plain_exchanges():
    segmenter = fake(ExternalServiceError("busy"))
    exchanges = checks.exchanges_as_pairs(turns())
    evaluator = fake(
        {
            "evaluations": [
                {"pair": n, "relevance": 50, "accuracy": None, "accuracy_basis": "not_applicable",
                 "completeness": 50, "articulation": 50,
                 "justification": f"See [T{p['answer_turn_ids'][0]}]."
                 if p["answer_turn_ids"] else "",
                 "requirements": []}
                for n, p in enumerate(exchanges)
            ],
            "follow_ups": [],
        }
    )  # fmt: skip
    final = run(segmenter, evaluator)
    assert final["segmented_by"] == "exchanges"
    assert [p["question_turn_ids"] for p in final["pairs"]] == [[1], [3], [6], [8]]
    # the closing line has no answer, so nothing is scored for it
    assert final["evaluations"]["3"]["relevance"] is None


def test_a_justification_citing_another_pairs_line_goes_back():
    first = evaluation(turns())
    first["evaluations"][0]["justification"] = "Good answer [T7]."
    evaluator = fake(first, evaluation(turns()))
    final = run(fake(segmentation(turns())), evaluator)
    assert len(evaluator.calls) == 2
    assert "[T4]" in " ".join(evaluator.calls[1]["feedback"])
    assert "[T4]" in final["evaluations"]["0"]["justification"]


def test_reference_basis_needs_a_real_passage():
    ev = evaluation(turns())
    ev["evaluations"][1]["justification"] = "Mentions pytest fixtures [T5] [S9]."
    final = run(fake(segmentation(turns())), fake(ev))
    assert final["evaluations"]["1"]["accuracy_basis"] == "general"


def test_no_candidate_lines_means_nothing_to_analyse():
    segmenter = fake(segmentation(turns()))
    only_interviewer = [t for t in turns() if t["speaker_role"] == "interviewer"]
    final = run(segmenter, fake(evaluation(turns())), t=only_interviewer)
    assert final["outcome"] == "empty"
    assert segmenter.calls == []


def test_the_evaluation_failing_fails_the_run():
    final = run(fake(segmentation(turns())), fake(ExternalServiceError("busy")))
    assert final["outcome"] == "failed"


# --- service and api -------------------------------------------------------------------


@pytest.fixture
def ended(db_session):
    job = Job(title="Backend Engineer")
    db_session.add(job)
    db_session.flush()
    reqs = {
        key: JobRequirement(job_id=job.id, text=text, category=RequirementCategory.TECHNICAL_SKILL,
                            is_mandatory=key == "django")
        for key, text in [("django", "Django web development"), ("k8s", "Kubernetes in production"),
                          ("testing", "Automated testing")]
    }  # fmt: skip
    candidate = Candidate(full_name="Ada Lovelace", email="ada@example.com")
    db_session.add_all([*reqs.values(), candidate])
    db_session.flush()
    start = datetime.now(UTC) - timedelta(hours=1)
    interview = Interview(
        job_id=job.id,
        candidate_id=candidate.id,
        status=InterviewStatus.IN_PROGRESS,
        scheduled_start=start,
        scheduled_end=start + timedelta(minutes=45),
        started_at=start,
        join_token=hash_token("candidate-link"),
    )
    db_session.add(interview)
    db_session.flush()
    for seq, (role, text) in enumerate(LINES, start=1):
        db_session.add(
            TranscriptTurn(interview_id=interview.id, sequence=seq, speaker_role=SpeakerRole(role),
                           text=text, start_offset_ms=seq * 1000, end_offset_ms=seq * 1000 + 500)
        )  # fmt: skip
    db_session.commit()
    return interview, {k: r.id for k, r in reqs.items()}


@pytest.fixture
def analysis_graph(monkeypatch: pytest.MonkeyPatch):
    holder: dict = {}

    @contextmanager
    def graph():
        yield build_analysis_graph(
            segmenter=holder["segmenter"], evaluator=holder["evaluator"], retriever=retriever
        )

    monkeypatch.setattr("app.worker.tasks._analysis_graph", graph)
    return holder


def _end(db_session, interview, monkeypatch):
    monkeypatch.setattr("app.tools.livekit.close_room", lambda room: None)
    interview_sessions.end(db_session, interview.id)


def test_ending_an_interview_queues_its_analysis(db_session, ended, monkeypatch, analyses_queued):
    interview, _ = ended
    _end(db_session, interview, monkeypatch)
    assert analyses_queued == [interview.id]


def test_the_analysis_runs_after_the_end_and_is_readable(
    client: TestClient,
    interviewer_token: str,
    db_session,
    ended,
    monkeypatch,
    real_analysis_queue,
    inline_jobs,
    analysis_graph,
):
    interview, req = ended
    t = [
        {"id": r.id, "speaker_role": r.speaker_role.value, "text": r.text}
        for r in db_session.scalars(select(TranscriptTurn).order_by(TranscriptTurn.sequence))
    ]
    analysis_graph["segmenter"] = fake(segmentation(t))
    analysis_graph["evaluator"] = fake(evaluation(t, req))
    monkeypatch.setattr("app.services.interview_analysis.wait_for_transcript", lambda i: None)

    _end(db_session, interview, monkeypatch)
    assert inline_jobs[0]["kind"] == "interview_analysis"
    assert inline_jobs[0]["error"] is None, inline_jobs[0]["error"]

    body = client.get(
        f"/api/v1/interviews/{interview.id}/analysis", headers=auth_header(interviewer_token)
    ).json()
    assert body["analysis"]["status"] == "analysed" and body["analysis"]["stale"] is False
    assert [p["sequence"] for p in body["pairs"]] == [1, 2, 3]
    assert body["pairs"][2]["follow_up_to"] == 2
    assert body["pairs"][1]["sources"][0]["title"] == "Testing guide"
    assert body["pairs"][0]["score"] == round((85 + 100 + 70 + 80) / 4, 1)
    coverage = {c["requirement_id"]: c["status"] for c in body["coverage"]}
    assert coverage[req["k8s"]] == "not_explored" and coverage[req["testing"]] == "covered"
    assert body["summary"]["not_explored"] == 1
    assert body["follow_ups"][0]["requirement_text"] == "Kubernetes in production"
    # the turns the pairs cite come with it, for showing the quotes
    assert {x["id"] for x in body["turns"]} >= {t[3]["id"], t[6]["id"]}
    run_row = db_session.scalar(select(AgentRun).where(AgentRun.graph_name == "interview_analysis"))
    assert run_row.context["candidate_id"] == interview.candidate_id

    # a line saved afterwards marks the analysis out of date; analysing again replaces it
    db_session.add(
        TranscriptTurn(interview_id=interview.id, sequence=99, speaker_role=SpeakerRole.CANDIDATE,
                       text="One more thing.", start_offset_ms=99000, end_offset_ms=99500)
    )  # fmt: skip
    db_session.commit()
    stale = client.get(
        f"/api/v1/interviews/{interview.id}/analysis", headers=auth_header(interviewer_token)
    ).json()
    assert stale["analysis"]["stale"] is True
    again = client.post(
        f"/api/v1/interviews/{interview.id}/analysis", headers=auth_header(interviewer_token)
    )
    assert again.status_code == 202
    after = client.get(
        f"/api/v1/interviews/{interview.id}/analysis", headers=auth_header(interviewer_token)
    ).json()
    assert len(after["pairs"]) == 3 and after["analysis"]["stale"] is False


def test_a_failed_analysis_is_recorded(
    db_session, ended, monkeypatch, real_analysis_queue, inline_jobs, analysis_graph
):
    interview, _ = ended
    analysis_graph["segmenter"] = fake(segmentation(turns()))
    analysis_graph["evaluator"] = fake(ExternalServiceError("The AI service is busy."))
    monkeypatch.setattr("app.services.interview_analysis.wait_for_transcript", lambda i: None)
    _end(db_session, interview, monkeypatch)
    row = db_session.scalar(select(InterviewAnalysis))
    assert row.status == "failed" and "busy" in row.message
    assert inline_jobs[0]["error"] is not None


def test_only_an_ended_interview_is_analysed_and_only_by_interviewers(
    client: TestClient, interviewer_token: str, candidate_token: str, ended, inline_jobs
):
    interview, _ = ended
    path = f"/api/v1/interviews/{interview.id}/analysis"
    response = client.post(path, headers=auth_header(interviewer_token))
    assert response.status_code >= 400 and "ended" in response.json()["error"]["message"]
    assert client.get(path, headers=auth_header(candidate_token)).status_code == 403
    assert client.post(path, headers=auth_header(candidate_token)).status_code == 403
    assert inline_jobs == []
