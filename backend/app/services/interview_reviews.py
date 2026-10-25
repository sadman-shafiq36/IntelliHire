# the reviews written after an interview's analysis: the cv's claims checked against the
# interview, the interview performance score, and the quality of the interview itself.
# each is a background job queued when the analysis finishes; reading them calls no ai

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.checkpointer import thread_config
from app.agents.runner import new_thread_id, run_graph
from app.config import settings
from app.errors import ConflictError, ExternalServiceError, InvalidInputError, NotFoundError
from app.logging_config import get_logger
from app.models.analysis import InterviewAnalysis, InterviewReview, QaPair, QaRequirementLink
from app.models.candidate import Candidate, CvExtraction
from app.models.enums import InterviewStatus
from app.models.interview import Interview, TranscriptTurn
from app.models.screening import CandidateScore, Screening
from app.services import interview_analysis
from app.services.jobs import get_job
from app.worker import queue

log = get_logger(__name__)

ProgressFn = Callable[..., None]
STALE_RUN = timedelta(minutes=15)


@dataclass(frozen=True)
class Kind:
    graph_name: str
    job_kind: str
    working: str
    sees_cv: bool


KINDS = {
    "claims": Kind("claim_verification", "claim_verification", "Checking the CV's claims.", True),
    "interview_score": Kind(
        "interview_scoring", "interview_scoring", "Scoring the interview.", False
    ),
    "quality": Kind("interview_quality", "interview_quality", "Reviewing the interview.", False),
}


def _aware(value: datetime | None) -> datetime | None:
    return value if value is None or value.tzinfo else value.replace(tzinfo=UTC)


def _interview(db: Session, interview_id: int) -> Interview:
    interview = db.get(Interview, interview_id)
    if interview is None:
        raise NotFoundError("That interview does not exist.")
    return interview


def _analysis(db: Session, interview_id: int) -> InterviewAnalysis | None:
    return db.scalar(
        select(InterviewAnalysis).where(InterviewAnalysis.interview_id == interview_id)
    )


def _kind(kind: str) -> Kind:
    if kind not in KINDS:
        raise NotFoundError("That review does not exist.")
    return KINDS[kind]


def _row(db: Session, interview_id: int, kind: str) -> InterviewReview:
    row = db.scalar(
        select(InterviewReview).where(
            InterviewReview.interview_id == interview_id, InterviewReview.kind == kind
        )
    )
    if row is None:
        row = InterviewReview(interview_id=interview_id, kind=kind)
        db.add(row)
    return row


# --- starting --------------------------------------------------------------------------


def _enqueue(db: Session, interview_id: int, kind: str) -> str:
    row = _row(db, interview_id, kind)
    row.status, row.message = "waiting", None
    db.commit()
    return queue.enqueue(
        "app.worker.tasks.review_interview_task",
        KINDS[kind].job_kind,
        interview_id=interview_id,
        review_kind=kind,
    )


def queue_after_analysis(db: Session, interview_id: int) -> None:
    # a new analysis means new reviews; failing to queue them never fails the analysis
    for kind in KINDS:
        try:
            _enqueue(db, interview_id, kind)
        except Exception:
            log.warning("review_not_queued", interview_id=interview_id, kind=kind)
            db.rollback()


def _check_ready(db: Session, interview: Interview) -> InterviewAnalysis:
    if interview.status != InterviewStatus.COMPLETED:
        raise InvalidInputError("An interview is reviewed once it has ended.")
    analysis = _analysis(db, interview.id)
    if analysis is None or analysis.status != "analysed":
        raise InvalidInputError(
            "The interview has to be analysed first. Open its analysis and press Analyse."
        )
    return analysis


def start(db: Session, interview_id: int, kind: str) -> str:
    _kind(kind)
    _check_ready(db, _interview(db, interview_id))
    row = db.scalar(
        select(InterviewReview).where(
            InterviewReview.interview_id == interview_id, InterviewReview.kind == kind
        )
    )
    if (
        row is not None
        and row.status in ("waiting", "running")
        and _aware(row.updated_at) > datetime.now(UTC) - STALE_RUN
    ):
        raise ConflictError("This review is already being written.")
    return _enqueue(db, interview_id, kind)


# --- the graphs' inputs ----------------------------------------------------------------


def _requirements(job: Any) -> list[dict[str, Any]]:
    return [
        {
            "id": r.id,
            "text": r.text,
            "category": r.category.value,
            "is_mandatory": r.is_mandatory,
            "weight": r.weight,
        }
        for r in job.requirements
    ]


def _all_turns(db: Session, interview_id: int) -> list[dict[str, Any]]:
    rows = db.scalars(
        select(TranscriptTurn)
        .where(TranscriptTurn.interview_id == interview_id)
        .order_by(TranscriptTurn.start_offset_ms, TranscriptTurn.sequence)
    )
    return [
        {
            "id": t.id,
            "speaker_role": t.speaker_role.value,
            "text": t.text,
            "start_offset_ms": t.start_offset_ms,
            "end_offset_ms": t.end_offset_ms,
        }
        for t in rows
        if t.stt_provider != "none" and t.text.strip()
    ]


def _pairs(db: Session, interview_id: int, requirements: dict[int, str]) -> list[dict[str, Any]]:
    rows = interview_analysis._pairs(db, interview_id)
    index_of = {p.id: i for i, p in enumerate(rows)}
    return [
        {
            "question": p.question_text,
            "question_turn_ids": p.question_turn_ids or [],
            "answer_turn_ids": p.answer_turn_ids or [],
            "follow_up_of": index_of.get(p.follow_up_to_id),
            "score": interview_analysis.pair_score(p),
            "relevance": p.relevance_score,
            "accuracy": p.accuracy_score,
            "completeness": p.completeness_score,
            "articulation": p.articulation_score,
            "requirements": [
                {
                    "requirement_id": link.job_requirement_id,
                    "text": requirements.get(link.job_requirement_id, ""),
                    "coverage": link.coverage.value,
                }
                for link in p.requirement_links
            ],
        }
        for p in rows
    ]


def _cv(db: Session, interview: Interview) -> tuple[str, int | None]:
    screening = db.scalar(
        select(Screening).where(
            Screening.job_id == interview.job_id, Screening.candidate_id == interview.candidate_id
        )
    )
    if not screening or not screening.cv_document_id:
        return "", None
    extraction = db.scalar(
        select(CvExtraction).where(CvExtraction.cv_document_id == screening.cv_document_id)
    )
    # stored with name, email and phone already removed
    return (extraction.raw_text or "") if extraction else "", screening.cv_document_id


def _peers(db: Session, interview: Interview) -> list[dict[str, Any]]:
    # the job's other analysed interviews, as the requirements each explored; no names
    ids = db.scalars(
        select(Interview.id)
        .join(InterviewAnalysis, InterviewAnalysis.interview_id == Interview.id)
        .where(
            Interview.job_id == interview.job_id,
            Interview.id != interview.id,
            InterviewAnalysis.status == "analysed",
        )
    ).all()
    if not ids:
        return []
    explored: dict[int, set[int]] = {i: set() for i in ids}
    rows = db.execute(
        select(QaPair.interview_id, QaRequirementLink.job_requirement_id)
        .join(QaRequirementLink, QaRequirementLink.qa_pair_id == QaPair.id)
        .where(QaPair.interview_id.in_(ids))
    )
    for interview_id, requirement_id in rows:
        explored[interview_id].add(requirement_id)
    return [{"interview_id": i, "explored": sorted(e)} for i, e in explored.items()]


def build_state(db: Session, interview: Interview, kind: str) -> dict[str, Any]:
    job = get_job(db, interview.job_id)
    requirements = _requirements(job)
    texts = {r["id"]: r["text"] for r in requirements}
    base: dict[str, Any] = {
        "interview_id": interview.id,
        "job": {"title": job.title},
        "requirements": requirements,
    }
    turns = _all_turns(db, interview.id)
    if kind == "claims":
        cv_text, cv_document_id = _cv(db, interview)
        return {
            **base,
            "cv_text": cv_text,
            "cv_document_id": cv_document_id,
            "turns": [
                {"id": t["id"], "speaker_role": t["speaker_role"], "text": t["text"]} for t in turns
            ],
        }
    pairs = _pairs(db, interview.id, texts)
    if kind == "interview_score":
        return {**base, "pairs": pairs, "turns": turns}
    return {
        **base,
        "pairs": pairs,
        "turns": turns,
        "peers": _peers(db, interview),
        "pause_ms": settings.speech_silence_ms,
    }


# --- running ---------------------------------------------------------------------------


def _graph_input(kind: str, state: dict[str, Any]) -> dict[str, Any]:
    # cv_document_id is for the audit row, not the graph
    return {k: v for k, v in state.items() if k != "cv_document_id"}


def _result(kind: str, final: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    if kind == "claims":
        from app.agents.claims.checks import settle

        claims = settle(final.get("claims") or [], final.get("verdicts") or {})
        return {"claims": claims}
    if kind == "interview_score":
        from app.agents.interview_score.checks import LABELS, WEIGHTS

        dimensions = final.get("dimensions") or {}
        return {
            "overall": final.get("overall"),
            "dimensions": [
                {"name": n, "label": LABELS[n], **dimensions[n]} for n in WEIGHTS if n in dimensions
            ],
        }
    questions = final.get("questions") or {}
    return {
        "scores": final.get("scores") or {},
        "metrics": final.get("metrics") or {},
        "questions": [
            {
                "sequence": i + 1,
                "question": p["question"],
                "question_turn_ids": p["question_turn_ids"],
                **(questions.get(str(i)) or {}),
            }
            for i, p in enumerate(state["pairs"])
        ],
        "recommendations": final.get("recommendations") or [],
        "summary": final.get("summary") or "",
    }


def _prompt_version(kind: str) -> str:
    if kind == "claims":
        from app.agents.claims.llm import PROMPT_VERSION
    elif kind == "interview_score":
        from app.agents.interview_score.llm import PROMPT_VERSION
    else:
        from app.agents.interview_quality.llm import PROMPT_VERSION
    return PROMPT_VERSION


def review(
    db: Session,
    interview_id: int,
    kind: str,
    graph: Any,
    progress: ProgressFn | None = None,
) -> dict[str, Any]:
    spec = _kind(kind)
    interview = _interview(db, interview_id)
    analysis = _check_ready(db, interview)
    row = _row(db, interview_id, kind)
    row.status, row.message = "running", None
    db.commit()
    if progress:
        progress(state="running", message=spec.working, total=0, completed=0)

    state = build_state(db, interview, kind)
    thread_id = new_thread_id(spec.graph_name.replace("_", "-"))
    initial = {"thread_id": thread_id, **_graph_input(kind, state), "errors": []}
    context: dict[str, Any] = {
        "interview_id": interview_id,
        # the graphs read the transcript, and the claims graph the cv, so deletion must find
        # these threads
        "candidate_id": interview.candidate_id,
        "job_id": interview.job_id,
        "prompt_version": _prompt_version(kind),
    }
    if spec.sees_cv:
        context["cv_document_id"] = state.get("cv_document_id")

    try:
        final = run_graph(
            db,
            graph_name=spec.graph_name,
            thread_id=thread_id,
            invoke=lambda: graph.invoke(initial, config=thread_config(thread_id)),
            context=context,
            failure_code=lambda s: None if s.get("outcome") in ("done", "empty") else "failed",
        )
    except ExternalServiceError as exc:
        row.status, row.message = "failed", exc.message[:500]
        db.commit()
        raise

    outcome = final.get("outcome")
    if outcome == "failed" or outcome not in ("done", "empty"):
        message = final.get("reason") or "The review could not be completed."
        row.status, row.message = "failed", message[:500]
        db.commit()
        raise ExternalServiceError(message)

    row.status = outcome
    row.message = ((final.get("reason") or "")[:500] or None) if outcome == "empty" else None
    row.result = _result(kind, final, state) if outcome == "done" else None
    row.warnings = sorted(set(final.get("warnings") or []))
    row.model_name = final.get("model_name") or None
    row.prompt_version = _prompt_version(kind)
    row.generated_at = datetime.now(UTC)
    row.based_on_analysed_at = analysis.analysed_at
    db.commit()
    log.info("interview_reviewed", interview_id=interview_id, kind=kind, outcome=outcome)
    return {"interview_id": interview_id, "kind": kind, "outcome": outcome}


# --- reading ---------------------------------------------------------------------------


def _out(row: InterviewReview | None, analysis: InterviewAnalysis | None) -> dict | None:
    if row is None:
        return None
    return {
        "status": row.status,
        "message": row.message,
        "result": row.result,
        "warnings": row.warnings or [],
        "model_name": row.model_name,
        "generated_at": _aware(row.generated_at),
        # the analysis was redone after this review was written
        "stale": bool(
            analysis
            and analysis.analysed_at
            and row.based_on_analysed_at
            and _aware(analysis.analysed_at) > _aware(row.based_on_analysed_at)
        ),
    }


def results(db: Session, interview_id: int) -> dict[str, Any]:
    interview = _interview(db, interview_id)
    job = get_job(db, interview.job_id)
    candidate = db.get(Candidate, interview.candidate_id)
    analysis = _analysis(db, interview_id)
    rows = {
        r.kind: r
        for r in db.scalars(
            select(InterviewReview).where(InterviewReview.interview_id == interview_id)
        )
    }
    score = db.scalar(
        select(CandidateScore).where(
            CandidateScore.job_id == job.id, CandidateScore.candidate_id == interview.candidate_id
        )
    )
    # every line a review cites, so the page can show who said it and when
    cited: set[int] = set()
    for row in rows.values():
        result = row.result or {}
        for c in result.get("claims") or []:
            cited |= {e["turn_id"] for e in c.get("evidence") or []}
        for d in result.get("dimensions") or []:
            cited |= {e["turn_id"] for e in d.get("evidence") or []}
        for q in result.get("questions") or []:
            cited |= set(q.get("question_turn_ids") or [])
    turns = (
        db.scalars(select(TranscriptTurn).where(TranscriptTurn.id.in_(cited))).all()
        if cited
        else []
    )
    return {
        "interview": {
            "id": interview.id,
            "job_id": job.id,
            "job_title": job.title,
            "candidate_id": interview.candidate_id,
            "candidate_name": candidate.full_name if candidate else "",
            "status": interview.status.value,
        },
        "analysis_status": analysis.status if analysis else None,
        "cv_score": score.aggregate_score if score else None,
        "requirements": [{"id": r.id, "text": r.text} for r in job.requirements],
        "turns": [
            {
                "id": t.id,
                "speaker_role": t.speaker_role.value,
                "text": t.text,
                "start_offset_ms": t.start_offset_ms,
            }
            for t in sorted(turns, key=lambda t: (t.start_offset_ms, t.sequence))
        ],
        **{kind: _out(rows.get(kind), analysis) for kind in KINDS},
    }
