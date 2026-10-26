from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import select

from app.agents.feedback.graph import build_feedback_graph
from app.models.analysis import AgentRun, Assessment, AssessmentFinding, QaPair, QaRequirementLink
from app.models.candidate import Candidate, CvDocument, CvExtraction
from app.models.enums import (
    CoverageStatus,
    EvidenceType,
    FindingKind,
    InterviewStatus,
    RequirementCategory,
    RequirementMatchStatus,
    ScoreDimensionName,
    ScreeningStatus,
)
from app.models.feedback import CandidateFeedback
from app.models.interview import Interview
from app.models.job import Job, JobRequirement
from app.models.screening import (
    CandidateScore,
    ScoreDimension,
    Screening,
    ScreeningRequirementResult,
)
from app.services import candidate_feedback
from app.services.candidates import delete_candidate
from app.services.interviews import hash_token
from app.tools.mcp_client import ToolCallError
from tests.conftest import auth_header

CV = (
    "Backend engineer. Built REST services in Django for five years. Wrote integration tests "
    "with pytest and ran services on virtual machines. Mentored two junior developers. "
) * 3


def _candidate(db, job, name, email, statuses, rank, selected=False):
    candidate = Candidate(full_name=name, email=email)
    db.add(candidate)
    db.flush()
    doc = CvDocument(candidate_id=candidate.id, job_id=job["job"].id, original_filename="cv.pdf",
                     storage_path="/tmp/none.pdf", content_type="application/pdf",
                     size_bytes=1)  # fmt: skip
    db.add(doc)
    db.flush()
    db.add(CvExtraction(cv_document_id=doc.id, raw_text=CV))
    screening = Screening(job_id=job["job"].id, candidate_id=candidate.id, cv_document_id=doc.id,
                          status=ScreeningStatus.SCREENED, rank=rank)  # fmt: skip
    db.add(screening)
    db.flush()
    for req, st in zip((job["django"], job["k8s"]), statuses, strict=True):
        db.add(
            ScreeningRequirementResult(
                screening_id=screening.id,
                job_requirement_id=req.id,
                status=st,
                evidence_quote="Built REST services in Django" if st.value == "met" else None,
                evidence_verified=st.value == "met",
                reasoning="No container orchestration in the CV." if st.value != "met" else "",
                confidence=0.85,
            )
        )
    score = CandidateScore(job_id=job["job"].id, candidate_id=candidate.id,
                           aggregate_score=90.0 - rank, rank=rank,
                           selected_at=datetime.now(UTC) if selected else None)  # fmt: skip
    score.dimensions = [ScoreDimension(dimension=ScoreDimensionName.TECHNICAL_SKILLS,
                                       raw_score=70.0,
                                       justification="Django yes, no Kubernetes.")]  # fmt: skip
    db.add(score)
    db.flush()
    return candidate


@pytest.fixture
def setup(db_session):
    db = db_session
    job = Job(title="Backend Engineer")
    db.add(job)
    db.flush()
    django = JobRequirement(job_id=job.id, text="Django web development", is_mandatory=True,
                            category=RequirementCategory.TECHNICAL_SKILL, weight=2)  # fmt: skip
    k8s = JobRequirement(job_id=job.id, text="Kubernetes in production", is_mandatory=True,
                         category=RequirementCategory.TECHNICAL_SKILL)  # fmt: skip
    db.add_all([django, k8s])
    db.flush()
    s = {"job": job, "django": django, "k8s": k8s}
    met, unmet = RequirementMatchStatus.MET, RequirementMatchStatus.UNMET
    # top of the ranking, never marked: must never get screening feedback by inference
    s["top"] = _candidate(db, s, "Ada Lovelace", "ada@example.com", (met, met), 1)
    s["picked"] = _candidate(db, s, "Grace Hopper", "grace@example.com", (met, met), 2,
                             selected=True)  # fmt: skip
    s["low"] = _candidate(db, s, "Alan Turing", "alan@example.com", (met, unmet), 3)
    s["other"] = _candidate(db, s, "Edsger Dijkstra", None, (unmet, unmet), 4)

    # an interviewed and assessed candidate
    start = datetime.now(UTC) - timedelta(hours=2)
    interview = Interview(job_id=job.id, candidate_id=s["picked"].id,
                          status=InterviewStatus.COMPLETED, scheduled_start=start,
                          scheduled_end=start + timedelta(minutes=45), started_at=start,
                          ended_at=start + timedelta(minutes=40),
                          join_token=hash_token("link"))  # fmt: skip
    db.add(interview)
    db.flush()
    pair = QaPair(interview_id=interview.id, sequence=1, question_text="Tell me about Django.",
                  answer_text="I built the payments API.", relevance_score=90,
                  justification="A concrete example.")  # fmt: skip
    pair.requirement_links = [
        QaRequirementLink(job_requirement_id=django.id, coverage=CoverageStatus.COVERED)
    ]
    db.add(pair)
    assessment = Assessment(interview_id=interview.id, job_id=job.id,
                            candidate_id=s["picked"].id, status="assessed",
                            overall_score=61.0, overall_position="possible_fit")  # fmt: skip
    assessment.findings = [
        AssessmentFinding(kind=FindingKind.EVIDENCE_GAP, text="No Kubernetes evidence.",
                          evidence_type=EvidenceType.SCORE, evidence_ref_id=k8s.id,
                          evidence=[{"type": "score", "ref_id": k8s.id,
                                     "quote": "Kubernetes in production"}])
    ]  # fmt: skip
    db.add(assessment)
    db.commit()
    s["interview"] = interview
    return s


def drafter(payload):
    sentences = []
    for p in payload["points"]:
        if p["kind"] == "strength":
            text = f"You showed clear experience with {p['requirement']}."
        else:
            text = f"Your CV didn't clearly show {p['requirement']}."
        sentences.append({"text": text, "point_ids": [p["id"]]})
    return {"sentences": sentences, "closing": "Thank you for your interest."}, "fake-model"


@pytest.fixture
def feedback_graph(monkeypatch: pytest.MonkeyPatch):
    # one saver for the whole test, so a review resumes the run it interrupted
    saver = InMemorySaver()
    calls: list[dict] = []

    def tracked(payload):
        calls.append(payload)
        return drafter(payload)

    def note_screen(notes):
        return {n["id"]: {"note_id": n["id"], "job_related": True, "reason": "work"} for n in notes}

    @contextmanager
    def graph():
        yield build_feedback_graph(saver, drafter=tracked, judge=lambda p: [],
                                   note_screen=note_screen)  # fmt: skip

    monkeypatch.setattr("app.worker.tasks._feedback_graph", graph)
    return calls


class FakeTools:
    channel = "smtp"

    def __init__(self, fail: bool = False) -> None:
        self.sent: list[dict] = []
        self.fail = fail

    def call(self, name, arguments):
        if self.fail:
            raise ToolCallError("The scheduling service could not be reached.")
        self.sent.append({"tool": name, **arguments})
        return {"message_id": "<m1@local>", "channel": "smtp"}


@pytest.fixture
def tools(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    from app.config import settings

    fake = FakeTools()
    monkeypatch.setattr(candidate_feedback, "_tools", lambda db: fake)
    monkeypatch.setattr(settings, "storage_dir", str(tmp_path))
    return fake


def test_feedback_is_for_interviewers_only(client: TestClient, candidate_token: str, setup):
    h = auth_header(candidate_token)
    job_id, cid = setup["job"].id, setup["low"].id
    calls = [
        ("post", f"/api/v1/candidates/{cid}/feedback", {"job_id": job_id, "stage": "screening"}),
        ("get", "/api/v1/feedback/1", None),
        ("post", "/api/v1/feedback/1/review", {"action": "approve"}),
        ("post", "/api/v1/feedback/1/send", None),
        ("get", f"/api/v1/interviews/{setup['interview'].id}/feedback", None),
        ("get", f"/api/v1/jobs/{job_id}/feedback", None),
        ("post", f"/api/v1/jobs/{job_id}/feedback/not-selected", {"candidate_ids": [cid]}),
        ("delete", f"/api/v1/jobs/{job_id}/feedback/not-selected/{cid}", None),
        ("post", f"/api/v1/jobs/{job_id}/feedback/screening-batch", None),
    ]
    for method, path, body in calls:
        kwargs = {"headers": h, **({"json": body} if body else {})}
        assert getattr(client, method)(path, **kwargs).status_code == 403, path


def test_screening_feedback_is_only_for_people_marked_not_selected(
    client: TestClient, interviewer_token: str, setup, feedback_graph, inline_jobs, db_session
):
    h = auth_header(interviewer_token)
    job_id = setup["job"].id
    # the top-ranked and the bottom-ranked alike: no mark, no feedback
    for c in (setup["top"], setup["low"]):
        r = client.post(f"/api/v1/candidates/{c.id}/feedback", headers=h,
                        json={"job_id": job_id, "stage": "screening"})  # fmt: skip
        assert r.status_code == 422 and "not selected" in r.json()["error"]["message"]
    # someone selected for interview can't be marked
    r = client.post(f"/api/v1/jobs/{job_id}/feedback/not-selected", headers=h,
                    json={"candidate_ids": [setup["picked"].id]})  # fmt: skip
    assert r.status_code == 409
    assert inline_jobs == []

    r = client.post(f"/api/v1/jobs/{job_id}/feedback/not-selected", headers=h,
                    json={"candidate_ids": [setup["low"].id]})  # fmt: skip
    assert r.json() == {"marked": 1}
    r = client.post(f"/api/v1/candidates/{setup['low'].id}/feedback", headers=h,
                    json={"job_id": job_id, "stage": "screening"})  # fmt: skip
    assert r.status_code == 202, r.text
    assert inline_jobs[0]["error"] is None, inline_jobs[0]["error"]
    fid = r.json()["feedback_id"]

    d = client.get(f"/api/v1/feedback/{fid}", headers=h).json()
    assert d["status"] == "in_review" and d["outcome"] == "not_progressing"
    assert d["guardrail"]["passed"] is True
    gap = next(p for p in d["points"] if p["kind"] == "development")
    assert gap["requirement_id"] == setup["k8s"].id
    assert gap["evidence_ref"]["type"] == "screening_result" and gap["evidence_ref"]["found"]
    assert gap["support"][0]["type"] == "score_dimension"
    assert "Your CV didn't clearly show Kubernetes" in d["draft"]["body"]
    # the drafter never saw a score, a name or the cv
    payload = str(feedback_graph[0])
    assert "Alan" not in payload and "85" not in payload and "five years" not in payload
    run = db_session.scalar(select(AgentRun).where(AgentRun.graph_name == "candidate_feedback"))
    assert run.context["candidate_id"] == setup["low"].id

    queue = client.get(f"/api/v1/jobs/{job_id}/feedback?stage=screening", headers=h).json()
    assert [(q["candidate_name"], q["status"]) for q in queue] == [("Alan Turing", "in_review")]


def test_batch_drafts_every_marked_candidate(
    client: TestClient, interviewer_token: str, setup, feedback_graph, inline_jobs
):
    h = auth_header(interviewer_token)
    job_id = setup["job"].id
    assert (
        client.post(f"/api/v1/jobs/{job_id}/feedback/screening-batch", headers=h).status_code == 422
    )
    client.post(f"/api/v1/jobs/{job_id}/feedback/not-selected", headers=h,
                json={"candidate_ids": [setup["low"].id, setup["other"].id]})  # fmt: skip
    r = client.post(f"/api/v1/jobs/{job_id}/feedback/screening-batch", headers=h)
    assert r.status_code == 202 and r.json()["candidate_count"] == 2
    assert inline_jobs[-1]["result"] == {"drafted": 2, "failed": 0}
    statuses = {
        q["status"] for q in client.get(f"/api/v1/jobs/{job_id}/feedback", headers=h).json()
    }
    assert statuses == {"in_review"}
    # unmarking takes back the unsent draft
    r = client.delete(f"/api/v1/jobs/{job_id}/feedback/not-selected/{setup['other'].id}",
                      headers=h)  # fmt: skip
    assert r.status_code == 204
    assert len(client.get(f"/api/v1/jobs/{job_id}/feedback", headers=h).json()) == 1


def test_interview_feedback_review_edit_approve_and_send_once(
    client: TestClient, interviewer_token: str, setup, feedback_graph, inline_jobs, tools,
    db_session,
):  # fmt: skip
    h = auth_header(interviewer_token)
    iv, cid = setup["interview"], setup["picked"].id
    base = {"job_id": setup["job"].id, "stage": "interview", "interview_id": iv.id}
    r = client.post(f"/api/v1/candidates/{cid}/feedback", headers=h, json=base)
    assert r.status_code == 422  # an outcome is required
    notes = [
        {"author": "interviewer", "text": "Seemed too old to keep up with our young team."},
        {"author": "interviewer", "text": "Clear, concrete answer on the Django payments API."},
    ]
    r = client.post(f"/api/v1/candidates/{cid}/feedback", headers=h,
                    json={**base, "outcome": "not_progressing", "notes": notes})  # fmt: skip
    assert r.status_code == 202, r.text
    fid = r.json()["feedback_id"]
    d = client.get(f"/api/v1/interviews/{iv.id}/feedback", headers=h).json()["feedback"]
    assert d["status"] == "in_review"
    assert [n["text"][:12] for n in d["excluded_notes"]] == ["Seemed too o"]
    assert [n["text"][:5] for n in feedback_graph[0]["notes"]] == ["Clear"]
    kinds = {p["kind"]: p for p in d["points"]}
    assert kinds["strength"]["evidence_ref"]["type"] == "qa_pair"
    assert kinds["development"]["evidence_ref"]["type"] == "assessment_finding"
    assert "decided not to move forward" in d["draft"]["body"]

    # nothing goes out before a person approves it
    assert client.post(f"/api/v1/feedback/{fid}/send", headers=h).status_code == 409

    bad = {"action": "edit", "subject": "Your interview",
           "body": "Dear {candidate_name}, you ranked third of five. "
                   "We will keep your CV on file."}  # fmt: skip
    r = client.post(f"/api/v1/feedback/{fid}/review", headers=h, json=bad)
    assert r.status_code == 202
    d = client.get(f"/api/v1/feedback/{fid}", headers=h).json()
    assert d["status"] == "in_review" and d["guardrail"]["passed"] is False
    assert {"scores", "promises"} <= {x["rule"] for x in d["guardrail"]["rules"] if not x["passed"]}
    r = client.post(f"/api/v1/feedback/{fid}/review", headers=h, json={"action": "approve"})
    assert r.status_code == 409

    good = {"action": "edit", "subject": "Your interview",
            "body": "Dear {candidate_name},\n\nThank you for the conversation about Django."
                    "\n\nBest regards,\n{signature}"}  # fmt: skip
    client.post(f"/api/v1/feedback/{fid}/review", headers=h, json=good)
    r = client.post(f"/api/v1/feedback/{fid}/review", headers=h, json={"action": "approve"})
    assert r.status_code == 200 and r.json()["status"] == "approved"

    sent = client.post(f"/api/v1/feedback/{fid}/send", headers=h)
    assert sent.status_code == 200 and sent.json()["send_channel"] == "smtp"
    email = tools.sent[0]
    assert email["to"] == "grace@example.com" and email["body"].startswith("Dear Grace Hopper,")
    assert "{" not in email["body"]
    assert client.post(f"/api/v1/feedback/{fid}/send", headers=h).status_code == 409
    assert len(tools.sent) == 1

    # the audit trail names actions and accounts, never the person
    row = db_session.get(CandidateFeedback, fid)
    actions = [e["action"] for e in row.events]
    assert actions[0] == "generate_requested" and actions[-2:] == ["approved", "sent"]
    assert "Grace" not in str(row.events) and "grace@" not in str(row.events)


def test_regenerate_and_discard(
    client: TestClient, interviewer_token: str, setup, feedback_graph, inline_jobs
):
    h = auth_header(interviewer_token)
    job_id = setup["job"].id
    client.post(f"/api/v1/jobs/{job_id}/feedback/not-selected", headers=h,
                json={"candidate_ids": [setup["low"].id]})  # fmt: skip
    fid = client.post(f"/api/v1/candidates/{setup['low'].id}/feedback", headers=h,
                      json={"job_id": job_id,
                            "stage": "screening"}).json()["feedback_id"]  # fmt: skip
    r = client.post(f"/api/v1/feedback/{fid}/review", headers=h,
                    json={"action": "regenerate", "instruction": "Shorter, please."})  # fmt: skip
    assert r.status_code == 202
    assert feedback_graph[-1]["request"] == "Shorter, please." and len(feedback_graph) == 2
    r = client.post(f"/api/v1/feedback/{fid}/review", headers=h, json={"action": "discard"})
    assert r.json()["status"] == "discarded"
    assert client.post(f"/api/v1/feedback/{fid}/send", headers=h).status_code == 409


def test_send_falls_back_to_the_outbox_and_deletion_removes_it(
    client: TestClient, interviewer_token: str, setup, feedback_graph, inline_jobs, tools,
    db_session,
):  # fmt: skip
    tools.fail = True
    h = auth_header(interviewer_token)
    job_id, cid = setup["job"].id, setup["low"].id
    client.post(f"/api/v1/jobs/{job_id}/feedback/not-selected", headers=h,
                json={"candidate_ids": [cid]})  # fmt: skip
    fid = client.post(f"/api/v1/candidates/{cid}/feedback", headers=h,
                      json={"job_id": job_id,
                            "stage": "screening"}).json()["feedback_id"]  # fmt: skip
    client.post(f"/api/v1/feedback/{fid}/review", headers=h, json={"action": "approve"})
    r = client.post(f"/api/v1/feedback/{fid}/send", headers=h)
    assert r.status_code == 200 and r.json()["send_channel"] == "outbox"
    path = Path(db_session.get(CandidateFeedback, fid).outbox_path)
    assert path.exists() and "Dear Alan Turing," in path.read_text()

    threads: list[str] = []
    delete_candidate(db_session, cid, delete_checkpoints=threads.extend)
    assert any(t.startswith("feedback-") for t in threads)
    assert db_session.scalars(select(CandidateFeedback)).all() == []
    assert not path.exists()
