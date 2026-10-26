# synthetic data to show the candidate feedback composer. every person, cv and answer here is
# made up. run inside the backend container:
#   python -m app.demo.seed_feedback            add the demo job and candidates
#   python -m app.demo.seed_feedback --remove   delete them again, with their checkpoints
#
# what it sets up, on the job "Platform Engineer (feedback demo)":
#   - an interviewed, analysed and assessed candidate whose interview notes include a biased
#     interviewer note, to show it being left out
#   - two candidates marked "not selected for interview": one with clear gaps, one whose cv
#     couldn't be read (the generic letter)
#   - the top-ranked candidate, not marked, so no screening feedback is ever drafted for them

import argparse
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.assessment.checks import compute
from app.db.session import get_session_factory
from app.models.analysis import (
    Assessment,
    AssessmentFinding,
    InterviewAnalysis,
    QaPair,
    QaRequirementLink,
)
from app.models.candidate import Candidate, CvDocument, CvExtraction
from app.models.enums import (
    CoverageStatus,
    EvidenceType,
    FindingKind,
    InterviewStatus,
    JobStatus,
    RequirementCategory,
    RequirementMatchStatus,
    ScoreDimensionName,
    ScreeningStatus,
    SpeakerRole,
)
from app.models.feedback import CandidateFeedback
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

TITLE = "Platform Engineer (feedback demo)"

REQUIREMENTS = [
    ("Python backend services in production", True, 2.0, RequirementCategory.TECHNICAL_SKILL),
    ("Kubernetes deployments in production", True, 1.0, RequirementCategory.TECHNICAL_SKILL),
    ("PostgreSQL query tuning", False, 1.0, RequirementCategory.TECHNICAL_SKILL),
    ("Mentoring other engineers", False, 1.0, RequirementCategory.OTHER),
]

CVS = {
    "rowan": (
        "Platform engineer with six years of experience. Built Python backend services in "
        "production with FastAPI and Django for a payments company. Deployed services to a "
        "managed Kubernetes cluster with Helm charts. Tuned slow PostgreSQL queries with "
        "EXPLAIN ANALYZE and partial indexes. Mentored two junior engineers through their "
        "first on-call rotation. Education: BSc Computer Science."
    ),
    "sam": (
        "Software developer with four years of experience. Built Python backend services in "
        "production for an online bookshop using Flask. Wrote integration tests with pytest. "
        "Ran the services on virtual machines managed by the operations team. Used MySQL "
        "through an ORM. Added caching to the catalogue search and cut page load times. "
        "Took part in code reviews and sprint planning. Education: BSc Information Systems."
    ),
    "kit": "Curriculum vitae  ##  [image]  [image]",
    "jordan": (
        "Senior backend engineer with eight years of experience. Built Python backend "
        "services in production at scale. Ran Kubernetes deployments in production across "
        "three regions. Tuned PostgreSQL query plans and connection pooling. Led a guild of "
        "five engineers and mentored new hires. Introduced service-level objectives and "
        "on-call runbooks. Education: MSc Software Engineering."
    ),
}

M, P, U = (
    RequirementMatchStatus.MET,
    RequirementMatchStatus.PARTIALLY_MET,
    RequirementMatchStatus.UNMET,
)
# per candidate, per requirement: status, quote, reasoning, confidence
RESULTS = {
    "rowan": [
        (M, "Built Python backend services in production", "Production Python services.", 0.9),
        (M, "Deployed services to a managed Kubernetes cluster", "Kubernetes deployments.", 0.8),
        (M, "Tuned slow PostgreSQL queries", "Query tuning described.", 0.85),
        (M, "Mentored two junior engineers", "Mentoring described.", 0.85),
    ],
    "sam": [
        (M, "Built Python backend services in production", "Production Python services.", 0.9),
        (U, None, "No container orchestration; services ran on virtual machines.", 0.85),
        (P, "Used MySQL through an ORM", "Relational database use, no query tuning.", 0.7),
        (U, None, "No mentoring or leading others mentioned.", 0.8),
    ],
    "kit": [
        (U, None, "Almost no readable text.", 0.2),
        (U, None, "Almost no readable text.", 0.2),
        (U, None, "Almost no readable text.", 0.2),
        (U, None, "Almost no readable text.", 0.2),
    ],
    "jordan": [
        (M, "Built Python backend services in production", "Production Python at scale.", 0.95),
        (M, "Ran Kubernetes deployments in production", "Multi-region Kubernetes.", 0.95),
        (M, "Tuned PostgreSQL query plans", "Query plan tuning.", 0.9),
        (M, "mentored new hires", "Leads and mentors.", 0.9),
    ],
}

PEOPLE = {
    "jordan": ("Jordan Pike (demo)", "jordan.pike@example.test", 1, 88.0),
    "rowan": ("Rowan Vale (demo)", "rowan.vale@example.test", 2, 81.0),
    "sam": ("Sam Ellery (demo)", "sam.ellery@example.test", 3, 58.0),
    "kit": ("Kit Marlow (demo)", None, 4, 20.0),
}

TRANSCRIPT = [
    (SpeakerRole.INTERVIEWER, "Tell me about a Python service you ran in production."),
    (
        SpeakerRole.CANDIDATE,
        "I built the refunds service in FastAPI. It handled about two "
        "hundred requests a second, and I added idempotency keys so retries were safe.",
    ),  # fmt: skip
    (SpeakerRole.INTERVIEWER, "How did you roll out a new version on Kubernetes?"),
    (
        SpeakerRole.CANDIDATE,
        "We used Helm. I'm honestly not sure how the rollout strategy "
        "was configured, the platform team set that up.",
    ),  # fmt: skip
    (SpeakerRole.INTERVIEWER, "How do you approach a slow PostgreSQL query?"),
    (
        SpeakerRole.CANDIDATE,
        "I start with EXPLAIN ANALYZE, look for sequential scans on big "
        "tables, and add a partial index when the filter is selective.",
    ),  # fmt: skip
]

NOTES = [
    {"id": "N1", "author": "interviewer",
     "text": "Seemed a bit old to keep up with our young team, and has kids so may want "
             "flexible hours."},
    {"id": "N2", "author": "interviewer",
     "text": "Concrete, well-reasoned answers on Python services and PostgreSQL tuning; vague "
             "on how Kubernetes rollouts were configured."},
]  # fmt: skip


def _existing(db: Session) -> Job | None:
    return db.scalar(select(Job).where(Job.title == TITLE))


def seed(db: Session) -> dict[str, Any]:
    if _existing(db):
        raise SystemExit("The feedback demo is already there. Remove it first with --remove.")
    job = Job(title=TITLE, department="Engineering", location="Remote", status=JobStatus.OPEN,
              min_years_experience=3,
              summary="Build and run the Python services behind our platform.")  # fmt: skip
    db.add(job)
    db.flush()
    reqs = []
    for text, must, weight, category in REQUIREMENTS:
        r = JobRequirement(job_id=job.id, text=text, is_mandatory=must, weight=weight,
                           category=category)  # fmt: skip
        db.add(r)
        reqs.append(r)
    db.flush()

    people: dict[str, Candidate] = {}
    for key, (name, email, rank, score_value) in PEOPLE.items():
        candidate = Candidate(full_name=name, email=email)
        db.add(candidate)
        db.flush()
        doc = CvDocument(candidate_id=candidate.id, job_id=job.id,
                         original_filename=f"demo-cv-{key}.pdf",
                         storage_path=f"/data/storage/cvs/demo-cv-{key}.pdf",
                         content_type="application/pdf", size_bytes=1)  # fmt: skip
        db.add(doc)
        db.flush()
        db.add(CvExtraction(cv_document_id=doc.id, raw_text=CVS[key], page_count=1))
        screening = Screening(job_id=job.id, candidate_id=candidate.id, cv_document_id=doc.id,
                              status=ScreeningStatus.SCREENED, rank=rank,
                              fulfilment_percent=score_value, screened_at=datetime.now(UTC),
                              model_name="demo-seed", prompt_version="demo")  # fmt: skip
        db.add(screening)
        db.flush()
        for r, (status, quote, reasoning, confidence) in zip(reqs, RESULTS[key], strict=True):
            db.add(ScreeningRequirementResult(
                screening_id=screening.id, job_requirement_id=r.id, status=status,
                evidence_quote=quote, evidence_verified=quote is not None,
                reasoning=reasoning, confidence=confidence))  # fmt: skip
        score = CandidateScore(job_id=job.id, candidate_id=candidate.id,
                               aggregate_score=score_value, rank=rank,
                               model_name="demo-seed", computed_at=datetime.now(UTC),
                               based_on_screened_at=screening.screened_at,
                               selected_at=datetime.now(UTC) if key == "rowan" else None,
                               is_shortlisted=key in ("rowan", "jordan"))  # fmt: skip
        score.dimensions = [
            ScoreDimension(dimension=ScoreDimensionName.TECHNICAL_SKILLS, raw_score=score_value,
                           base_score=score_value, weight=0.4,
                           justification=_justification(key)),
            ScoreDimension(dimension=ScoreDimensionName.RELEVANT_EXPERIENCE,
                           raw_score=score_value, base_score=score_value, weight=0.3,
                           justification="Years and kind of experience compared with the role."),
        ]  # fmt: skip
        db.add(score)
        people[key] = candidate
    db.flush()

    interview = _interview(db, job, reqs, people["rowan"])

    # the explicit marks a recruiter makes on the scoring step
    for key in ("sam", "kit"):
        db.add(CandidateFeedback(candidate_id=people[key].id, job_id=job.id, stage="screening",
                                 outcome="not_progressing", status="new", notes=[],
                                 marked_at=datetime.now(UTC),
                                 events=[_event("marked_not_selected")]))  # fmt: skip
    # the interview notes, one of them biased, waiting for someone to write the feedback
    db.add(CandidateFeedback(candidate_id=people["rowan"].id, job_id=job.id,
                             interview_id=interview.id, stage="interview",
                             outcome="not_progressing", status="new", notes=NOTES,
                             events=[_event("notes_added")]))  # fmt: skip
    db.commit()
    return {"job_id": job.id, "interview_id": interview.id}


def _justification(key: str) -> str:
    return {
        "rowan": "Python, Kubernetes and PostgreSQL work all shown in the CV.",
        "sam": "Python services shown; no container orchestration or query tuning.",
        "kit": "Too little readable text to judge.",
        "jordan": "Every technical requirement shown with specifics.",
    }[key]


def _event(action: str) -> dict[str, Any]:
    return {"at": datetime.now(UTC).isoformat(), "action": action, "user_id": None,
            "seeded": True}  # fmt: skip


def _interview(db: Session, job: Job, reqs: list[JobRequirement], candidate: Candidate):
    python, k8s, postgres, mentoring = reqs
    start = datetime.now(UTC) - timedelta(days=1)
    interview = Interview(job_id=job.id, candidate_id=candidate.id,
                          status=InterviewStatus.COMPLETED, scheduled_start=start,
                          scheduled_end=start + timedelta(minutes=45), started_at=start,
                          ended_at=start + timedelta(minutes=38),
                          join_token=hash_token(
                              f"demo-{candidate.id}-{start.timestamp()}"))  # fmt: skip
    db.add(interview)
    db.flush()
    turns = []
    for i, (role, text) in enumerate(TRANSCRIPT, start=1):
        t = TranscriptTurn(interview_id=interview.id, sequence=i, speaker_role=role,
                           speaker_label="Interviewer" if role == SpeakerRole.INTERVIEWER
                           else "Candidate", text=text, start_offset_ms=i * 60_000,
                           end_offset_ms=i * 60_000 + 20_000, stt_provider="demo")  # fmt: skip
        db.add(t)
        turns.append(t)
    db.flush()
    pairs = [
        (1, turns[0], turns[1], python, CoverageStatus.COVERED, (90, 85, 80, 85),
         "A concrete production example with idempotent retries [T{a}]."),
        (2, turns[2], turns[3], k8s, CoverageStatus.PARTIALLY_COVERED, (80, 50, 40, 70),
         "Named the tool but couldn't describe how rollouts were configured [T{a}]."),
        (3, turns[4], turns[5], postgres, CoverageStatus.COVERED, (90, 85, 80, 85),
         "A clear, correct method for diagnosing slow queries [T{a}]."),
    ]  # fmt: skip
    for seq, q, a, req, coverage, (rel, acc, comp, art), why in pairs:
        pair = QaPair(interview_id=interview.id, sequence=seq, question_text=q.text,
                      answer_text=a.text, question_turn_ids=[q.id], answer_turn_ids=[a.id],
                      relevance_score=rel, accuracy_score=acc, completeness_score=comp,
                      articulation_score=art, accuracy_basis="general",
                      justification=why.format(a=a.id))  # fmt: skip
        pair.requirement_links = [QaRequirementLink(job_requirement_id=req.id, coverage=coverage)]
        db.add(pair)
    analysed = datetime.now(UTC) - timedelta(hours=20)
    db.add(InterviewAnalysis(interview_id=interview.id, status="analysed", segmented_by="ai",
                             turn_count=len(turns), analysed_at=analysed,
                             model_name="demo-seed", prompt_version="demo"))  # fmt: skip

    requirements = [
        {"id": python.id, "text": python.text, "is_mandatory": True, "weight": 2.0,
         "cv_status": "met", "cv_quote": RESULTS["rowan"][0][1], "interview_status": "covered"},
        {"id": k8s.id, "text": k8s.text, "is_mandatory": True, "weight": 1.0,
         "cv_status": "met", "cv_quote": RESULTS["rowan"][1][1],
         "interview_status": "partially_covered"},
        {"id": postgres.id, "text": postgres.text, "is_mandatory": False, "weight": 1.0,
         "cv_status": "met", "cv_quote": RESULTS["rowan"][2][1], "interview_status": "covered"},
        {"id": mentoring.id, "text": mentoring.text, "is_mandatory": False, "weight": 1.0,
         "cv_status": "met", "cv_quote": RESULTS["rowan"][3][1],
         "interview_status": "not_explored"},
    ]  # fmt: skip
    components, overall, position, capped = compute(81.0, 73.8, requirements)
    assessment = Assessment(interview_id=interview.id, job_id=job.id, candidate_id=candidate.id,
                            status="assessed", overall_score=overall, overall_position=position,
                            component_scores={**components, "requirements": requirements,
                                              "capped_by": capped, "cv_score": 81.0,
                                              "answer_quality": 73.8},
                            summary="Strong, specific evidence for Python services and "
                                    "PostgreSQL tuning in both the CV and the interview. "
                                    "Kubernetes rollouts were described only at the level of "
                                    "tool names.",
                            model_name="demo-seed", prompt_version="demo",
                            generated_at=datetime.now(UTC),
                            based_on_analysed_at=analysed)  # fmt: skip
    assessment.findings = [
        AssessmentFinding(kind=FindingKind.STRENGTH,
                          text="Built and ran a production Python service with safe retries.",
                          evidence_type=EvidenceType.TRANSCRIPT, evidence_ref_id=turns[1].id,
                          evidence_quote="I added idempotency keys so retries were safe",
                          evidence=[{"type": "transcript", "ref_id": turns[1].id,
                                     "quote": "I added idempotency keys so retries were safe"}]),
        AssessmentFinding(kind=FindingKind.WEAKNESS,
                          text="Could not describe how Kubernetes rollouts were configured.",
                          evidence_type=EvidenceType.SCORE, evidence_ref_id=k8s.id,
                          evidence_quote=k8s.text,
                          evidence=[{"type": "score", "ref_id": k8s.id, "quote": k8s.text},
                                    {"type": "transcript", "ref_id": turns[3].id,
                                     "quote": "not sure how the rollout strategy was configured"}]),
    ]  # fmt: skip
    db.add(assessment)
    db.flush()
    return interview


def remove(db: Session) -> int:
    job = _existing(db)
    if job is None:
        return 0
    candidate_ids = set(
        db.scalars(select(Screening.candidate_id).where(Screening.job_id == job.id))
    )
    for candidate_id in candidate_ids:
        delete_candidate(db, candidate_id)
    db.delete(db.get(Job, job.id))
    db.commit()
    return len(candidate_ids)


def main() -> None:
    parser = argparse.ArgumentParser(description="Synthetic data for the feedback composer.")
    parser.add_argument("--remove", action="store_true", help="delete the demo data")
    args = parser.parse_args()
    with get_session_factory()() as db:
        if args.remove:
            print(f"Removed the feedback demo ({remove(db)} candidates).")
        else:
            ids = seed(db)
            print(
                f"Feedback demo added: job {ids['job_id']}, interview {ids['interview_id']}. "
                f"Open /jobs/{ids['job_id']}?tab=scoring and "
                f"/interviews/{ids['interview_id']}/assessment?view=feedback"
            )


if __name__ == "__main__":
    main()
