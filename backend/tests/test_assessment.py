from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.agents.assessment import checks
from app.agents.assessment.graph import build_assessment_graph
from app.errors import ExternalServiceError
from app.models.analysis import AgentRun, Assessment, InterviewAnalysis, QaPair, QaRequirementLink
from app.models.candidate import Candidate, CvDocument, CvExtraction
from app.models.enums import (
    CoverageStatus,
    InterviewStatus,
    RequirementCategory,
    RequirementMatchStatus,
    ScoreDimensionName,
    ScreeningStatus,
    SpeakerRole,
)
from app.models.interview import Interview, TranscriptTurn
from app.models.job import Job, JobRequirement
from app.models.screening import (
    CandidateScore,
    ScoreDimension,
    Screening,
    ScreeningRequirementResult,
)
from app.services.candidates import delete_candidate
from app.services.interviews import hash_token
from tests.conftest import auth_header

CV = "Backend engineer. Built REST services in Django for five years. Deployed with Docker on AWS."


def req(id_, text, must=False, cv=None, interview="not_explored", weight=1.0):
    return {"id": id_, "text": text, "is_mandatory": must, "weight": weight, "cv_status": cv,
            "cv_quote": None, "interview_status": interview}  # fmt: skip


# --- the numbers -----------------------------------------------------------------------


def test_components_weights_and_position():
    requirements = [
        req(1, "Django", must=True, cv="met", interview="covered", weight=2),
        req(2, "Kubernetes", cv="unmet", interview="partially_covered"),
        req(3, "Mentoring", cv="unmet"),
    ]
    components, overall, position, capped = checks.compute(80.0, 70.0, requirements)
    # coverage: (1*2 + 0.5*1 + 0*1) / 4
    assert components["requirement_coverage"]["score"] == 62.5
    assert overall == round(80 * 0.4 + 70 * 0.4 + 62.5 * 0.2, 1)
    assert position == "strong_fit" and capped == []


def test_a_must_have_without_any_evidence_caps_the_position():
    requirements = [req(1, "Django", must=True, cv="unmet"), req(2, "SQL", cv="met")]
    _, overall, position, capped = checks.compute(95.0, 95.0, requirements)
    assert overall >= checks.STRONG
    assert position == "possible_fit" and capped == ["Django"]


def test_a_missing_component_gives_its_weight_to_the_others():
    components, overall, position, _ = checks.compute(None, 60.0, [req(1, "SQL", cv="met")])
    assert components["cv_fit"]["effective_weight"] == 0.0
    assert components["interview_answers"]["effective_weight"] == round(100 * 40 / 60, 1)
    assert overall == round(60 * 40 / 60 + 100 * 20 / 60, 1)
    assert checks.compute(None, None, [])[2] == "not_assessed"


def test_findings_must_rest_on_evidence_that_is_there():
    turns = [{"id": 7, "speaker_role": "candidate", "text": "I used pytest fixtures everywhere."}]
    requirements = [{"id": 3, "text": "Mentoring"}]
    answer = {
        "summary": "Solid Django work; testing practice needs a closer look.",
        "findings": [
            {"kind": "strength", "text": "Long Django experience.",
             "evidence": [{"source": "cv", "quote": "Built REST services in Django"}]},
            {"kind": "weakness", "text": "Claims Kubernetes.",
             "evidence": [{"source": "cv", "quote": "ran Kubernetes clusters"}]},
            {"kind": "area_to_verify", "text": "How tests use fixtures.",
             "evidence": [{"source": "transcript", "quote": "pytest fixtures", "turn_id": 7},
                          {"source": "transcript", "quote": "pytest fixtures", "turn_id": 99}]},
            {"kind": "evidence_gap", "text": "Mentoring was never discussed.",
             "evidence": [{"source": "requirement", "requirement_id": 3, "quote": ""}]},
            {"kind": "weakness", "text": "We should not hire them.",
             "evidence": [{"source": "cv", "quote": "Built REST services in Django"}]},
        ],
    }  # fmt: skip
    summary, findings, problems = checks.check_findings(answer, CV, turns, requirements, 42)
    assert summary.startswith("Solid Django")
    assert [f["kind"] for f in findings] == ["strength", "area_to_verify", "evidence_gap"]
    assert findings[0]["evidence"] == [
        {"type": "cv", "ref_id": 42, "quote": "Built REST services in Django"}
    ]
    # the line that doesn't exist is dropped, the real one kept
    assert [e["ref_id"] for e in findings[1]["evidence"]] == [7]
    assert findings[2]["evidence"][0] == {"type": "score", "ref_id": 3, "quote": "Mentoring"}
    assert any("no evidence" in p for p in problems)
    assert any("recommends a decision" in p for p in problems)


def test_a_verdict_in_the_summary_is_refused():
    summary, _, problems = checks.check_findings(
        {"summary": "Strong engineer; we recommend hiring them.", "findings": []}, CV, [], [], None
    )
    assert summary == "" and problems


# --- the graph ---------------------------------------------------------------------------


def state(**over):
    base = {
        "thread_id": "assessment-test",
        "interview_id": 1,
        "job": {"title": "Backend Engineer"},
        "requirements": [req(1, "Django", must=True, cv="met", interview="covered")],
        "cv_score": 80.0,
        "answer_quality": 70.0,
        "pairs": [],
        "turns": [],
        "cv_text": CV,
        "cv_document_id": 42,
        "errors": [],
    }
    base.update(over)
    return base


GOOD = {
    "summary": "Built Django services for years.",
    "findings": [
        {"kind": "strength", "text": "Django experience.",
         "evidence": [{"source": "cv", "quote": "Built REST services in Django"}]}
    ],
}  # fmt: skip
BAD = {
    "summary": "Fine.",
    "findings": [
        {
            "kind": "strength",
            "text": "Rust.",
            "evidence": [{"source": "cv", "quote": "Rust expert"}],
        }
    ],
}


def fake(*answers):
    calls: list[dict] = []

    def writer(payload):
        calls.append(payload)
        a = answers[min(len(calls), len(answers)) - 1]
        if isinstance(a, Exception):
            raise a
        return a, "fake-model"

    writer.calls = calls  # type: ignore[attr-defined]
    return writer


def test_unproven_findings_go_back_once():
    writer = fake(BAD, GOOD)
    final = build_assessment_graph(writer=writer).invoke(state())
    assert len(writer.calls) == 2
    assert "no evidence" in " ".join(writer.calls[1]["feedback"])
    assert final["outcome"] == "assessed" and final["findings"][0]["text"] == "Django experience."
    # the writer is told the decided position and can't change it
    assert writer.calls[0]["position"] == "strong_fit"


def test_the_numbers_are_kept_when_the_ai_fails():
    final = build_assessment_graph(writer=fake(ExternalServiceError("busy"))).invoke(state())
    assert final["outcome"] == "assessed"
    assert not final.get("findings")
    assert final["overall"] is not None
    assert any("scores only" in w for w in final["warnings"])


def test_nothing_to_measure_fails():
    writer = fake(GOOD)
    final = build_assessment_graph(writer=writer).invoke(
        state(cv_score=None, answer_quality=None, requirements=[])
    )
    assert final["outcome"] == "failed" and writer.calls == []


# --- service and api -------------------------------------------------------------------


@pytest.fixture
def assessed_setup(db_session):
    job = Job(title="Backend Engineer")
    db_session.add(job)
    db_session.flush()
    django = JobRequirement(
        job_id=job.id,
        text="Django web development",
        is_mandatory=True,
        category=RequirementCategory.TECHNICAL_SKILL,
        weight=2,
    )
    k8s = JobRequirement(job_id=job.id, text="Kubernetes in production",
                         category=RequirementCategory.TECHNICAL_SKILL)  # fmt: skip
    candidate = Candidate(full_name="Ada Lovelace", email="ada@example.com")
    db_session.add_all([django, k8s, candidate])
    db_session.flush()
    doc = CvDocument(
        candidate_id=candidate.id,
        job_id=job.id,
        original_filename="cv.pdf",
        storage_path="/tmp/none.pdf",
        content_type="application/pdf",
        size_bytes=1,
    )
    db_session.add(doc)
    db_session.flush()
    db_session.add(CvExtraction(cv_document_id=doc.id, raw_text=CV))
    screening = Screening(job_id=job.id, candidate_id=candidate.id, cv_document_id=doc.id,
                          status=ScreeningStatus.SCREENED)  # fmt: skip
    db_session.add(screening)
    db_session.flush()
    db_session.add_all([
        ScreeningRequirementResult(screening_id=screening.id, job_requirement_id=django.id,
                                   status=RequirementMatchStatus.MET,
                                   evidence_quote="Built REST services in Django"),
        ScreeningRequirementResult(screening_id=screening.id, job_requirement_id=k8s.id,
                                   status=RequirementMatchStatus.UNMET),
    ])  # fmt: skip
    score = CandidateScore(job_id=job.id, candidate_id=candidate.id, aggregate_score=78.0)
    score.dimensions = [
        ScoreDimension(dimension=ScoreDimensionName.TECHNICAL_SKILLS, raw_score=78.0)
    ]
    db_session.add(score)
    start = datetime.now(UTC) - timedelta(hours=2)
    interview = Interview(job_id=job.id, candidate_id=candidate.id,
                          status=InterviewStatus.COMPLETED, scheduled_start=start,
                          scheduled_end=start + timedelta(minutes=45), started_at=start,
                          ended_at=start + timedelta(minutes=40),
                          join_token=hash_token("link"))  # fmt: skip
    db_session.add(interview)
    db_session.flush()
    q = TranscriptTurn(
        interview_id=interview.id,
        sequence=1,
        speaker_role=SpeakerRole.INTERVIEWER,
        text="Tell me about your Django work.",
        start_offset_ms=60000,
        end_offset_ms=62000,
    )
    a = TranscriptTurn(interview_id=interview.id, sequence=2, speaker_role=SpeakerRole.CANDIDATE,
                       text="I built the payments API in Django REST framework.",
                       start_offset_ms=63000, end_offset_ms=70000)  # fmt: skip
    db_session.add_all([q, a])
    db_session.flush()
    pair = QaPair(interview_id=interview.id, sequence=1, question_text=q.text, answer_text=a.text,
                  question_turn_ids=[q.id], answer_turn_ids=[a.id], relevance_score=90,
                  accuracy_score=80, completeness_score=70, articulation_score=80,
                  justification=f"Concrete example [T{a.id}].")  # fmt: skip
    pair.requirement_links = [
        QaRequirementLink(job_requirement_id=django.id, coverage=CoverageStatus.COVERED)
    ]
    db_session.add(pair)
    db_session.add(InterviewAnalysis(interview_id=interview.id, status="analysed", turn_count=2,
                                     analysed_at=datetime.now(UTC)))  # fmt: skip
    db_session.commit()
    return {"interview": interview, "candidate": candidate, "job": job, "turn": a,
            "k8s": k8s, "doc": doc}  # fmt: skip


@pytest.fixture
def assessment_graph(monkeypatch: pytest.MonkeyPatch):
    holder: dict = {}

    @contextmanager
    def graph():
        yield build_assessment_graph(writer=holder["writer"])

    monkeypatch.setattr("app.worker.tasks._assessment_graph", graph)
    return holder


def written(s) -> dict:
    return {
        "summary": "Clear Django evidence in both the CV and the interview; Kubernetes is open.",
        "findings": [
            {"kind": "strength", "text": "Built Django services.",
             "evidence": [{"source": "cv", "quote": "Built REST services in Django"},
                          {"source": "transcript", "quote": "payments API in Django REST framework",
                           "turn_id": s["turn"].id}]},
            {"kind": "evidence_gap", "text": "No Kubernetes evidence anywhere.",
             "evidence": [{"source": "requirement", "requirement_id": s["k8s"].id, "quote": ""}]},
        ],
    }  # fmt: skip


def test_report_export_and_comparison(
    client: TestClient,
    interviewer_token: str,
    assessed_setup,
    assessment_graph,
    inline_jobs,
    db_session,
):
    s = assessed_setup
    iv = s["interview"]
    assessment_graph["writer"] = fake(written(s))
    h = auth_header(interviewer_token)
    started = client.post(f"/api/v1/interviews/{iv.id}/assessment", headers=h)
    assert started.status_code == 202, started.text
    assert inline_jobs[0]["error"] is None, inline_jobs[0]["error"]

    body = client.get(f"/api/v1/interviews/{iv.id}/assessment", headers=h).json()
    a = body["assessment"]
    assert a["status"] == "assessed" and a["stale"] is False
    by_name = {c["name"]: c for c in a["components"]}
    assert by_name["cv_fit"]["score"] == 78.0
    assert by_name["interview_answers"]["score"] == 80.0
    # django (weight 2) met and covered, kubernetes (weight 1) neither: 2/3
    assert by_name["requirement_coverage"]["score"] == 66.7
    assert a["overall_score"] == round(78 * 0.4 + 80 * 0.4 + 66.7 * 0.2, 1)
    assert a["position"] == "strong_fit"
    strength = a["findings"][0]
    assert strength["kind"] == "strength"
    # transcript evidence comes back with who said it and when
    turn_ev = next(e for e in strength["evidence"] if e["type"] == "transcript")
    assert turn_ev["speaker_role"] == "candidate" and turn_ev["start_offset_ms"] == 63000
    run = db_session.scalar(select(AgentRun).where(AgentRun.graph_name == "candidate_assessment"))
    assert run.context["candidate_id"] == s["candidate"].id

    page = client.get(f"/api/v1/interviews/{iv.id}/assessment/report.html", headers=h)
    assert page.status_code == 200 and "Ada Lovelace" in page.text
    assert "<mark>Built REST services in Django</mark>" in page.text
    assert "Candidate, 1:03 into the interview" in page.text
    pdf = client.get(f"/api/v1/interviews/{iv.id}/assessment/report.pdf", headers=h)
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    assert "Ada" not in pdf.headers["content-disposition"]

    comparison = client.get(f"/api/v1/jobs/{s['job'].id}/assessments", headers=h).json()
    assert [c["full_name"] for c in comparison["candidates"]] == ["Ada Lovelace"]
    assert comparison["candidates"][0]["findings"]["evidence_gap"] == [
        "No Kubernetes evidence anywhere."
    ]

    overview = client.get("/api/v1/overview", headers=h).json()
    assert overview["pipeline"]["assessed"] == 1

    # a newer analysis makes the report out of date
    analysis = db_session.scalar(select(InterviewAnalysis))
    analysis.analysed_at = datetime.now(UTC) + timedelta(minutes=5)
    db_session.commit()
    again = client.get(f"/api/v1/interviews/{iv.id}/assessment", headers=h).json()
    assert again["assessment"]["stale"] is True

    # deleting the candidate removes the report too
    delete_candidate(db_session, s["candidate"].id, delete_checkpoints=lambda threads: None)
    assert db_session.scalars(select(Assessment)).all() == []


def test_an_unanalysed_interview_is_not_assessed(
    client: TestClient, interviewer_token: str, assessed_setup, inline_jobs, db_session
):
    db_session.delete(db_session.scalar(select(InterviewAnalysis)))
    db_session.commit()
    iv = assessed_setup["interview"]
    response = client.post(
        f"/api/v1/interviews/{iv.id}/assessment", headers=auth_header(interviewer_token)
    )
    assert response.status_code >= 400 and "analysed first" in response.json()["error"]["message"]
    assert inline_jobs == []


def test_assessments_are_for_interviewers_only(
    client: TestClient, candidate_token: str, assessed_setup
):
    iv, job = assessed_setup["interview"], assessed_setup["job"]
    h = auth_header(candidate_token)
    for path in [
        f"/api/v1/interviews/{iv.id}/assessment",
        f"/api/v1/interviews/{iv.id}/assessment/report.html",
        f"/api/v1/interviews/{iv.id}/assessment/report.pdf",
        f"/api/v1/jobs/{job.id}/assessments",
    ]:
        assert client.get(path, headers=h).status_code == 403, path
    assert client.post(f"/api/v1/interviews/{iv.id}/assessment", headers=h).status_code == 403
