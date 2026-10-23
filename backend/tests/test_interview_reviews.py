from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.agents.claims import checks as claim_checks
from app.agents.claims.graph import build_claims_graph
from app.agents.interview_quality import checks as quality_checks
from app.agents.interview_quality.graph import build_quality_graph
from app.agents.interview_score import checks as score_checks
from app.agents.interview_score.graph import build_interview_score_graph
from app.errors import ExternalServiceError
from app.models.analysis import (
    AgentRun,
    InterviewAnalysis,
    InterviewReview,
    QaPair,
    QaRequirementLink,
)
from app.models.candidate import Candidate, CvDocument, CvExtraction
from app.models.enums import (
    CoverageStatus,
    InterviewStatus,
    RequirementCategory,
    ScreeningStatus,
    SpeakerRole,
)
from app.models.interview import Interview, TranscriptTurn
from app.models.job import Job, JobRequirement
from app.models.screening import CandidateScore, Screening
from app.services.candidates import delete_candidate
from app.services.interviews import hash_token
from tests.conftest import auth_header

CV = (
    "Backend engineer. Built REST services in Django for five years. Ran Kubernetes clusters "
    "in production at Acme. Certified AWS Solutions Architect."
)
TURNS = [
    {"id": 1, "speaker_role": "interviewer", "text": "How did you run Kubernetes at Acme?"},
    {"id": 2, "speaker_role": "candidate",
     "text": "I have never used Kubernetes, we deployed on plain VMs."},
    {"id": 3, "speaker_role": "interviewer", "text": "Tell me about your Django services."},
    {"id": 4, "speaker_role": "candidate",
     "text": "I built the payments API in Django REST framework with Celery."},
]  # fmt: skip
CLAIMS = [
    {"claim": "Ran Kubernetes in production", "cv_quote": "Ran Kubernetes clusters in production",
     "topic": "Kubernetes"},
    {"claim": "Five years of Django", "cv_quote": "Built REST services in Django",
     "topic": "Django"},
    {"claim": "AWS certification", "cv_quote": "Certified AWS Solutions Architect", "topic": "AWS"},
]  # fmt: skip


def fake(*answers):
    calls: list[dict] = []

    def step(payload):
        calls.append(payload)
        a = answers[min(len(calls), len(answers)) - 1]
        if isinstance(a, Exception):
            raise a
        return a, "fake-model"

    step.calls = calls  # type: ignore[attr-defined]
    return step


# --- cv claims ---------------------------------------------------------------------------


def test_a_claim_must_be_quoted_from_the_cv():
    raw = [
        *CLAIMS[:1],
        {"claim": "Led a team of ten", "cv_quote": "Led a team of ten engineers", "topic": "Lead"},
        {"claim": "Married with kids", "cv_quote": "Backend engineer", "topic": "family"},
    ]
    kept, problems = claim_checks.check_claims(raw, CV)
    assert [c["topic"] for c in kept] == ["Kubernetes"]
    assert any("word for word" in p for p in problems)
    assert any("personal characteristic" in p for p in problems)


def test_verdicts_need_the_candidates_own_words():
    raw = [
        {"claim": 0, "status": "contradiction", "explanation": "Said they never used it.",
         "follow_up": "Could you clarify where Kubernetes was used?",
         "evidence": [{"turn_id": 2, "quote": "I have never used Kubernetes"}]},
        # an interviewer line is not evidence
        {"claim": 1, "status": "supported", "explanation": "Discussed.", "follow_up": None,
         "evidence": [{"turn_id": 3, "quote": "your Django services"}]},
        {"claim": 2, "status": "not_discussed", "explanation": "Not raised.",
         "follow_up": "Which AWS certification do you hold?", "evidence": []},
    ]  # fmt: skip
    verdicts, problems = claim_checks.check_verdicts(raw, CLAIMS, TURNS)
    assert verdicts["0"]["status"] == "contradiction"
    assert verdicts["0"]["evidence"] == [{"turn_id": 2, "quote": "I have never used Kubernetes"}]
    assert "1" not in verdicts and any("Claim 1" in p for p in problems)
    assert verdicts["2"]["status"] == "not_discussed" and verdicts["2"]["evidence"] == []


def test_flags_are_never_accusations():
    raw = [
        {"claim": 0, "status": "contradiction",
         "explanation": "The candidate lied about Kubernetes.",
         "follow_up": "Why did you exaggerate?",
         "evidence": [{"turn_id": 2, "quote": "I have never used Kubernetes"}]},
        {"claim": 2, "status": "not_discussed", "explanation": "Never came up.", "follow_up": "",
         "evidence": []},
    ]  # fmt: skip
    verdicts, problems = claim_checks.check_verdicts(raw, CLAIMS, TURNS)
    assert "0" not in verdicts and any("never accuse" in p for p in problems)
    # a missing follow-up gets a neutral one that quotes the cv
    assert verdicts["2"]["follow_up"].startswith('Your CV mentions "Certified AWS')


def test_a_quoted_claim_cannot_be_not_discussed():
    quote = {"turn_id": 4, "quote": "payments API in Django REST framework"}
    raw = [{"claim": 1, "status": "not_discussed", "explanation": "", "follow_up": None,
            "evidence": [quote]}]  # fmt: skip
    verdicts, problems = claim_checks.check_verdicts(raw, CLAIMS, TURNS)
    assert verdicts == {} and "so it was discussed" in problems[0]


def claims_state(**over):
    base = {"thread_id": "claims-test", "interview_id": 1, "job": {"title": "Backend Engineer"},
            "requirements": [{"id": 1, "text": "Kubernetes"}], "cv_text": CV, "turns": TURNS,
            "errors": []}  # fmt: skip
    base.update(over)
    return base


GOOD_VERDICTS = {
    "verdicts": [
        {"claim": 0, "status": "contradiction", "explanation": "They said Kubernetes wasn't used.",
         "follow_up": "Could you tell me how the Acme clusters were run?",
         "evidence": [{"turn_id": 2, "quote": "I have never used Kubernetes"}]},
        {"claim": 1, "status": "supported", "explanation": "Described a Django payments API.",
         "follow_up": None,
         "evidence": [{"turn_id": 4, "quote": "payments API in Django REST framework"}]},
        {"claim": 2, "status": "not_discussed", "explanation": "AWS never came up.",
         "follow_up": "Which AWS certification do you hold, and how have you used it?",
         "evidence": []},
    ]
}  # fmt: skip


def test_claims_graph_sends_bad_quotes_back_and_settles_every_claim():
    bad = {"claims": [{"claim": "Rust", "cv_quote": "Rust expert", "topic": "Rust"}]}
    extractor = fake(bad, {"claims": CLAIMS})
    comparer = fake({"verdicts": GOOD_VERDICTS["verdicts"][:2]}, GOOD_VERDICTS)
    final = build_claims_graph(extractor=extractor, comparer=comparer).invoke(claims_state())
    assert final["outcome"] == "done"
    assert len(extractor.calls) == 2 and "word for word" in extractor.calls[1]["feedback"][0]
    # the second comparison only had to fill the missing claim
    assert len(comparer.calls) == 2 and "missing: 2" in comparer.calls[1]["feedback"][0]
    rows = claim_checks.settle(final["claims"], final["verdicts"])
    assert [r["status"] for r in rows] == ["contradiction", "supported", "not_discussed"]


def test_claims_without_a_cv_or_with_a_failing_comparison():
    empty = build_claims_graph(extractor=fake({"claims": CLAIMS}), comparer=fake(GOOD_VERDICTS))
    assert empty.invoke(claims_state(cv_text=""))["outcome"] == "empty"
    graph = build_claims_graph(
        extractor=fake({"claims": CLAIMS}), comparer=fake(ExternalServiceError("busy"))
    )
    final = graph.invoke(claims_state())
    assert final["outcome"] == "done"
    rows = claim_checks.settle(final["claims"], final.get("verdicts") or {})
    assert {r["status"] for r in rows} == {"not_checked"}
    assert any("couldn't be checked" in w for w in final["warnings"])


# --- interview score ---------------------------------------------------------------------

PAIRS = [
    {"question": "Kubernetes?", "question_turn_ids": [1], "answer_turn_ids": [2],
     "follow_up_of": None, "score": 60.0, "relevance": 80, "accuracy": 70, "completeness": 50,
     "articulation": 60, "requirements": [{"requirement_id": 1, "text": "Kubernetes",
                                           "coverage": "partially_covered"}]},
    {"question": "Django?", "question_turn_ids": [3], "answer_turn_ids": [4], "follow_up_of": None,
     "score": 85.0, "relevance": 90, "accuracy": 90, "completeness": 80, "articulation": 80,
     "requirements": [{"requirement_id": 2, "text": "Django", "coverage": "covered"}]},
]  # fmt: skip
REQS = [
    {"id": 1, "text": "Kubernetes", "is_mandatory": False, "weight": 1.0,
     "category": "technical_skill"},
    {"id": 2, "text": "Django", "is_mandatory": True, "weight": 2.0,
     "category": "technical_skill"},
    {"id": 3, "text": "Mentoring", "is_mandatory": False, "weight": 1.0, "category": "soft_skill"},
]  # fmt: skip


def test_baselines_come_from_the_analysis():
    base = score_checks.baselines(PAIRS, REQS)
    assert base["technical_competency"]["base"] == 80.0
    assert base["relevance"]["base"] == 85.0
    assert base["communication"]["base"] == 70.0
    # kubernetes partly (0.5 * 1) + django covered (1 * 2) over weight 4
    assert base["job_alignment"]["base"] == 62.5
    assert base["problem_solving"]["base"] is None


def test_the_ai_moves_a_measured_score_a_little_and_only_with_a_quote():
    base = score_checks.baselines(PAIRS, REQS)
    quote = [{"turn_id": 4, "quote": "payments API in Django REST framework"}]
    raw = [
        {"dimension": "technical_competency", "score": 100, "reason": "Strong.", "evidence": quote},
        {"dimension": "relevance", "score": 40, "reason": "Off.", "evidence": []},
        {"dimension": "problem_solving", "score": 70, "reason": "Explained Celery.",
         "evidence": [{"turn_id": 4, "quote": "with Celery"}]},
        # an interviewer line is not the candidate's evidence
        {"dimension": "depth_of_experience", "score": 90, "reason": "Deep.",
         "evidence": [{"turn_id": 3, "quote": "your Django services"}]},
        {"dimension": "communication", "score": None, "reason": "", "evidence": []},
        {"dimension": "job_alignment", "score": 62, "reason": "", "evidence": quote},
    ]  # fmt: skip
    dims, problems = score_checks.check_judgements(raw, base, TURNS)
    assert dims["technical_competency"]["score"] == 90.0  # 80 + capped 10
    assert dims["technical_competency"]["adjustment"] == 10
    assert dims["relevance"]["score"] == 85.0 and dims["relevance"]["adjustment"] == 0
    assert dims["problem_solving"]["score"] == 70
    assert "depth_of_experience" not in dims and any("exact words" in p for p in problems)
    assert dims["communication"]["score"] == 70.0


def test_overall_shares_weight_when_a_dimension_was_not_shown():
    dims = {n: {"score": 80.0} for n in score_checks.WEIGHTS}
    dims["problem_solving"]["score"] = None
    overall, effective = score_checks.aggregate(dims)
    assert overall == 80.0 and effective["problem_solving"] == 0.0
    assert effective["technical_competency"] == round(100 * 25 / 80, 1)


def test_score_graph_keeps_measured_scores_when_the_ai_fails():
    state = {"thread_id": "score-test", "interview_id": 1, "job": {"title": "Backend Engineer"},
             "requirements": REQS, "pairs": PAIRS, "turns": TURNS, "errors": []}  # fmt: skip
    final = build_interview_score_graph(judge=fake(ExternalServiceError("busy"))).invoke(state)
    assert final["outcome"] == "done"
    assert final["dimensions"]["problem_solving"]["score"] is None
    assert final["dimensions"]["relevance"]["score"] == 85.0
    assert final["overall"] is not None and any("measured" in w for w in final["warnings"])


# --- interview quality -------------------------------------------------------------------


def timed(id_, role, text, start, end):
    return {"id": id_, "speaker_role": role, "text": text, "start_offset_ms": start,
            "end_offset_ms": end}  # fmt: skip


QTURNS = [
    timed(1, "interviewer", "How did you run Kubernetes at Acme? " * 4, 0, 40000),
    timed(2, "candidate", "Never used it.", 39000, 42000),
    timed(3, "interviewer", "Tell me about your Django services.", 43000, 46000),
    timed(4, "candidate", "I built the payments API in Django REST framework with Celery "
          "workers, idempotent webhooks and a reconciliation job.", 47000, 77000),
]  # fmt: skip


def test_measures_speaking_time_overlaps_and_low_signal_questions():
    peers = [{"interview_id": 9, "explored": [2, 3]}]
    m = quality_checks.measure(QTURNS, PAIRS, REQS, peers)
    assert m["speaking_ms"] == {"interviewer": 43000, "candidate": 33000}
    assert m["candidate_share"] == 43
    assert m["overlaps"] == 1
    assert m["low_signal"] == [{"pair": 0, "reasons": ["very short answer"]}]
    assert m["coverage"] == 75.0
    # this interview explored {1, 2}, the other {2, 3}
    assert m["consistency"] == round(100 / 3, 1)
    issues = {i["id"]: i for i in quality_checks.issues(m, PAIRS, REQS)}
    assert {"balance", "unexplored:3", "low_signal:0", "consistency"} <= set(issues)
    assert "100% of this job's other interviews" in issues["unexplored:3"]["evidence"]


def test_review_is_checked_against_the_question_and_the_issues():
    m = quality_checks.measure(QTURNS, PAIRS, REQS, [])
    issue_list = quality_checks.issues(m, PAIRS, REQS)
    raw = {
        "questions": [
            {"question": 0, "signal": "low", "style": "closed",
             "reason": "A yes/no prompt [T1], see also [T4].", "better_question":
             "Walk me through a deployment you ran and how you monitored it?"},
            {"question": 1, "signal": "high", "style": "open", "reason": "Concrete detail [T4].",
             "better_question": None},
        ],
        "recommendations": [
            {"issue": "balance", "text": "Ask shorter questions so the candidate talks more."},
            {"issue": "made-up", "text": "Something else."},
        ],
        "summary": "Two questions; the first drew little.",
    }  # fmt: skip
    questions, recs, summary, problems = quality_checks.check_review(raw, PAIRS, issue_list)
    assert problems == []
    # a citation outside the question's own lines is removed
    assert questions["0"]["reason"] == "A yes/no prompt [T1], see also ."
    assert questions["1"]["better_question"] == ""
    by_issue = {r["issue"]: r for r in recs}
    assert by_issue["balance"]["written_by"] == "ai"
    assert by_issue["unexplored:3"]["written_by"] == "rule"
    assert "made-up" not in by_issue
    scores = quality_checks.scores(m, questions, len(PAIRS))
    assert scores["question_effectiveness"] == 60.0
    assert scores["balance"] == 100 - 3 * (60 - 43)
    assert scores["consistency"] is None


def test_quality_graph_falls_back_to_rule_advice():
    state = {"thread_id": "quality-test", "interview_id": 1, "job": {"title": "Backend Engineer"},
             "requirements": REQS, "pairs": PAIRS, "turns": QTURNS, "peers": [],
             "errors": []}  # fmt: skip
    final = build_quality_graph(reviewer=fake(ExternalServiceError("busy"))).invoke(state)
    assert final["outcome"] == "done"
    assert final["recommendations"] and {r["written_by"] for r in final["recommendations"]} == {
        "rule"
    }
    # without the ai, effectiveness is the share of questions that gave something to go on
    assert final["scores"]["question_effectiveness"] == 50.0


# --- service and api ---------------------------------------------------------------------


@pytest.fixture
def reviewed_setup(db_session):
    job = Job(title="Backend Engineer")
    db_session.add(job)
    db_session.flush()
    k8s = JobRequirement(job_id=job.id, text="Kubernetes in production", is_mandatory=True,
                         category=RequirementCategory.TECHNICAL_SKILL)  # fmt: skip
    django = JobRequirement(job_id=job.id, text="Django web development",
                            category=RequirementCategory.TECHNICAL_SKILL, weight=2)  # fmt: skip
    candidate = Candidate(full_name="Ada Lovelace", email="ada@example.com")
    db_session.add_all([k8s, django, candidate])
    db_session.flush()
    doc = CvDocument(candidate_id=candidate.id, job_id=job.id, original_filename="cv.pdf",
                     storage_path="/tmp/none.pdf", content_type="application/pdf",
                     size_bytes=1)  # fmt: skip
    db_session.add(doc)
    db_session.flush()
    db_session.add(CvExtraction(cv_document_id=doc.id, raw_text=CV))
    db_session.add(Screening(job_id=job.id, candidate_id=candidate.id, cv_document_id=doc.id,
                             status=ScreeningStatus.SCREENED))  # fmt: skip
    db_session.add(CandidateScore(job_id=job.id, candidate_id=candidate.id, aggregate_score=78.0))
    start = datetime.now(UTC) - timedelta(hours=2)
    interview = Interview(job_id=job.id, candidate_id=candidate.id,
                          status=InterviewStatus.COMPLETED, scheduled_start=start,
                          scheduled_end=start + timedelta(minutes=45), started_at=start,
                          ended_at=start + timedelta(minutes=40),
                          join_token=hash_token("link"))  # fmt: skip
    db_session.add(interview)
    db_session.flush()
    rows = []
    for i, t in enumerate(QTURNS, start=1):
        row = TranscriptTurn(interview_id=interview.id, sequence=i,
                             speaker_role=SpeakerRole(t["speaker_role"]), text=t["text"],
                             start_offset_ms=t["start_offset_ms"],
                             end_offset_ms=t["end_offset_ms"])  # fmt: skip
        db_session.add(row)
        rows.append(row)
    db_session.flush()
    for seq, (q, a, req, coverage) in enumerate(
        [(rows[0], rows[1], k8s, CoverageStatus.PARTIALLY_COVERED),
         (rows[2], rows[3], django, CoverageStatus.COVERED)], start=1
    ):  # fmt: skip
        pair = QaPair(interview_id=interview.id, sequence=seq, question_text=q.text,
                      answer_text=a.text, question_turn_ids=[q.id], answer_turn_ids=[a.id],
                      relevance_score=80, accuracy_score=80, completeness_score=70,
                      articulation_score=70, justification=f"[T{a.id}]")  # fmt: skip
        pair.requirement_links = [QaRequirementLink(job_requirement_id=req.id, coverage=coverage)]
        db_session.add(pair)
    db_session.add(InterviewAnalysis(interview_id=interview.id, status="analysed", turn_count=4,
                                     analysed_at=datetime.now(UTC)))  # fmt: skip
    db_session.commit()
    return {"interview": interview, "candidate": candidate, "job": job, "turns": rows,
            "doc": doc}  # fmt: skip


@pytest.fixture
def review_graphs(monkeypatch: pytest.MonkeyPatch, reviewed_setup):
    turns = reviewed_setup["turns"]
    claims = {"claims": CLAIMS[:2]}
    verdicts = {
        "verdicts": [
            {"claim": 0, "status": "contradiction", "explanation": "They said it wasn't used.",
             "follow_up": "Could you tell me how the clusters at Acme were run?",
             "evidence": [{"turn_id": turns[1].id, "quote": "Never used it"}]},
            {"claim": 1, "status": "supported", "explanation": "Described a Django API.",
             "follow_up": None,
             "evidence": [{"turn_id": turns[3].id, "quote": "payments API in Django"}]},
        ]
    }  # fmt: skip
    judgements = {
        "judgements": [
            {"dimension": "problem_solving", "score": 75, "reason": "Idempotent webhooks.",
             "evidence": [{"turn_id": turns[3].id, "quote": "idempotent webhooks"}]},
        ]
    }  # fmt: skip
    review = {"questions": [
        {"question": 0, "signal": "low", "style": "leading", "reason": f"[T{turns[0].id}]",
         "better_question": "What container platform did you deploy to, and how?"},
        {"question": 1, "signal": "high", "style": "open", "reason": "", "better_question": None},
    ], "recommendations": [], "summary": "A short technical interview."}  # fmt: skip

    @contextmanager
    def graph(kind):
        yield {
            "claims": lambda: build_claims_graph(extractor=fake(claims), comparer=fake(verdicts)),
            "interview_score": lambda: build_interview_score_graph(judge=fake(judgements)),
            "quality": lambda: build_quality_graph(reviewer=fake(review)),
        }[kind]()

    monkeypatch.setattr("app.worker.tasks._review_graph", graph)


def test_reviews_run_and_read_back(
    client: TestClient,
    interviewer_token: str,
    reviewed_setup,
    review_graphs,
    inline_jobs,
    db_session,
):
    s = reviewed_setup
    iv = s["interview"]
    h = auth_header(interviewer_token)
    for kind in ("claims", "interview_score", "quality"):
        started = client.post(f"/api/v1/interviews/{iv.id}/reviews/{kind}", headers=h)
        assert started.status_code == 202, started.text
    assert [c["error"] for c in inline_jobs] == [None, None, None]

    body = client.get(f"/api/v1/interviews/{iv.id}/reviews", headers=h).json()
    assert body["cv_score"] == 78.0
    claims = body["claims"]["result"]["claims"]
    assert [c["status"] for c in claims] == ["contradiction", "supported"]
    assert claims[0]["follow_up"].endswith("?")
    # cited lines come back so the page can show them
    assert s["turns"][1].id in {t["id"] for t in body["turns"]}

    score = body["interview_score"]["result"]
    by_name = {d["name"]: d for d in score["dimensions"]}
    assert by_name["problem_solving"]["score"] == 75
    assert by_name["depth_of_experience"]["score"] is None
    assert by_name["technical_competency"]["score"] == 80.0
    assert score["overall"] is not None

    quality = body["quality"]["result"]
    assert quality["questions"][0]["signal"] == "low"
    assert quality["metrics"]["candidate_share"] == 43
    assert any(r["issue"] == "balance" for r in quality["recommendations"])

    runs = db_session.scalars(
        select(AgentRun).where(AgentRun.graph_name == "claim_verification")
    ).all()
    assert runs[0].context["cv_document_id"] == s["doc"].id
    assert runs[0].context["candidate_id"] == s["candidate"].id

    # a newer analysis makes every review out of date
    analysis = db_session.scalar(select(InterviewAnalysis))
    analysis.analysed_at = datetime.now(UTC) + timedelta(minutes=5)
    db_session.commit()
    again = client.get(f"/api/v1/interviews/{iv.id}/reviews", headers=h).json()
    assert again["claims"]["stale"] and again["quality"]["stale"]

    delete_candidate(db_session, s["candidate"].id, delete_checkpoints=lambda threads: None)
    assert db_session.scalars(select(InterviewReview)).all() == []


def test_reviews_need_an_analysis_and_an_interviewer(
    client: TestClient, interviewer_token: str, candidate_token: str, reviewed_setup, db_session
):
    iv = reviewed_setup["interview"]
    h = auth_header(candidate_token)
    assert client.get(f"/api/v1/interviews/{iv.id}/reviews", headers=h).status_code == 403
    assert client.post(f"/api/v1/interviews/{iv.id}/reviews/claims", headers=h).status_code == 403
    db_session.delete(db_session.scalar(select(InterviewAnalysis)))
    db_session.commit()
    response = client.post(
        f"/api/v1/interviews/{iv.id}/reviews/claims", headers=auth_header(interviewer_token)
    )
    assert response.status_code == 422 and "analysed first" in response.json()["error"]["message"]
    unknown = client.post(
        f"/api/v1/interviews/{iv.id}/reviews/other", headers=auth_header(interviewer_token)
    )
    assert unknown.status_code == 422


def test_reviews_say_the_candidate_not_he_or_she():
    base = score_checks.baselines(PAIRS, REQS)
    quote = [{"turn_id": 4, "quote": "payments API in Django REST framework"}]
    raw = [{"dimension": "problem_solving", "score": 70, "reason": "He explained the design.",
            "evidence": quote}]  # fmt: skip
    dims, problems = score_checks.check_judgements(raw, base, TURNS)
    assert "problem_solving" not in dims and any("the candidate" in p for p in problems)
    verdicts, problems = claim_checks.check_verdicts(
        [{"claim": 1, "status": "supported", "explanation": "She described the API.",
          "follow_up": None, "evidence": quote}], CLAIMS, TURNS,
    )  # fmt: skip
    assert verdicts == {} and problems
    # technical words that only look like pronouns are fine
    assert score_checks.check_judgements(
        [{"dimension": "problem_solving", "score": 70, "reason": "Explained the hereditary "
          "schema and the helm chart.", "evidence": quote}], base, TURNS,
    )[0]["problem_solving"]["score"] == 70  # fmt: skip


def test_the_balance_issue_says_who_talked_too_much():
    m = quality_checks.measure(QTURNS, PAIRS, REQS, [])
    balance = next(i for i in quality_checks.issues(m, PAIRS, REQS) if i["id"] == "balance")
    assert balance["title"] == "The interviewer did 57% of the talking"
    assert "only 43%" in balance["evidence"]


def test_the_pause_that_ends_a_line_is_not_speech_or_overlap():
    m = quality_checks.measure(QTURNS, PAIRS, REQS, [], pause_ms=700)
    assert m["speaking_ms"] == {"interviewer": 43000 - 1400, "candidate": 33000 - 1400}
    # the candidate started 1 s before the interviewer's line "ended": within pause + margin
    assert m["overlaps"] == 0
