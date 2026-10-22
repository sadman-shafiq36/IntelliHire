# the interviewer's co-pilot: on request, the copilot graph suggests what to ask next from
# the job's requirements, the screening and scores, the redacted cv and the transcript so
# far. suggestions are saved per batch; the interviewer marks the ones they asked

from collections.abc import Callable
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents.checkpointer import thread_config
from app.agents.copilot.checks import SUGGESTION_COUNT
from app.agents.copilot.llm import PROMPT_VERSION
from app.agents.runner import new_thread_id, run_graph
from app.errors import ExternalServiceError, InvalidInputError, NotFoundError
from app.logging_config import get_logger
from app.models.analysis import CopilotSuggestion
from app.models.candidate import CvExtraction
from app.models.enums import InterviewStatus
from app.models.interview import Interview, TranscriptTurn
from app.models.job import JobRequirement
from app.models.screening import CandidateScore, Screening
from app.services.jobs import get_job

log = get_logger(__name__)

ProgressFn = Callable[..., None]
MAX_FOCUS_CHARS = 200

# what the panel says while each node runs
NEXT_STEP = {
    "plan": "Searching the reference material.",
    "retrieve": "Writing questions.",
    "suggest": "Checking the questions against the CV and the transcript.",
    "verify": "Questions checked.",
}


def _interview(db: Session, interview_id: int) -> Interview:
    interview = db.get(Interview, interview_id)
    if interview is None:
        raise NotFoundError("That interview does not exist.")
    return interview


def check_can_suggest(db: Session, interview_id: int) -> Interview:
    interview = _interview(db, interview_id)
    if interview.status not in (InterviewStatus.SCHEDULED, InterviewStatus.IN_PROGRESS):
        raise InvalidInputError(
            "The co-pilot works before and during an interview, not after it has ended."
        )
    return interview


def clean_focus(focus: str | None) -> str | None:
    focus = " ".join((focus or "").split())[:MAX_FOCUS_CHARS]
    return focus or None


def _turns(db: Session, interview_id: int) -> list[dict[str, Any]]:
    rows = db.scalars(
        select(TranscriptTurn)
        .where(TranscriptTurn.interview_id == interview_id)
        .order_by(TranscriptTurn.start_offset_ms, TranscriptTurn.sequence)
    )
    return [{"id": t.id, "speaker_role": t.speaker_role.value, "text": t.text} for t in rows]


def build_state(db: Session, interview: Interview, focus: str | None) -> dict[str, Any]:
    job = get_job(db, interview.job_id)
    screening = db.scalar(
        select(Screening).where(
            Screening.job_id == job.id, Screening.candidate_id == interview.candidate_id
        )
    )
    results: dict[str, dict[str, Any]] = {}
    cv_text = ""
    if screening is not None:
        results = {
            str(r.job_requirement_id): {
                "status": r.status.value,
                "evidence_quote": r.evidence_quote,
            }
            for r in screening.requirement_results
        }
        if screening.cv_document_id:
            extraction = db.scalar(
                select(CvExtraction).where(CvExtraction.cv_document_id == screening.cv_document_id)
            )
            # raw_text is stored with name, email and phone already removed
            cv_text = (extraction.raw_text or "") if extraction else ""
    score = db.scalar(
        select(CandidateScore).where(
            CandidateScore.job_id == job.id, CandidateScore.candidate_id == interview.candidate_id
        )
    )
    asked = db.scalars(
        select(CopilotSuggestion.question_text)
        .where(CopilotSuggestion.interview_id == interview.id)
        .order_by(CopilotSuggestion.id)
    ).all()
    return {
        "interview_id": interview.id,
        "job": {"title": job.title},
        "requirements": [
            {
                "id": r.id,
                "text": r.text,
                "category": r.category.value,
                "is_mandatory": r.is_mandatory,
            }
            for r in job.requirements
        ],
        "screening": results,
        "dimension_scores": (
            {d.dimension.value: round(d.raw_score) for d in score.dimensions} if score else {}
        ),
        "cv_text": cv_text,
        "turns": _turns(db, interview.id),
        "already_asked": list(asked),
        "focus": focus,
    }


def suggest(
    db: Session,
    interview_id: int,
    focus: str | None,
    graph: Any,
    progress: ProgressFn | None = None,
) -> dict[str, Any]:
    interview = check_can_suggest(db, interview_id)
    thread_id = new_thread_id("copilot")
    initial = {"thread_id": thread_id, **build_state(db, interview, clean_focus(focus))}

    def report(message: str) -> None:
        if progress:
            progress(state="running", message=message, total=0, completed=0)

    def invoke() -> dict[str, Any]:
        final: dict[str, Any] = {}
        report("Choosing which requirements to probe.")
        for mode, chunk in graph.stream(
            initial, config=thread_config(thread_id), stream_mode=["updates", "values"]
        ):
            if mode == "values":
                final = chunk
                continue
            for node, update in chunk.items():
                if node == "verify" and len((update or {}).get("suggestions") or []) < (
                    SUGGESTION_COUNT
                ):
                    report("Some questions didn't pass the checks. Writing more.")
                elif node in NEXT_STEP:
                    report(NEXT_STEP[node])
        return final

    final = run_graph(
        db,
        graph_name="interview_copilot",
        thread_id=thread_id,
        invoke=invoke,
        # the graph reads the cv, so deleting the candidate must find this thread
        context={
            "interview_id": interview.id,
            "candidate_id": interview.candidate_id,
            "job_id": interview.job_id,
            "prompt_version": PROMPT_VERSION,
        },
        failure_code=lambda s: None if s.get("outcome") == "suggested" else s.get("outcome"),
    )
    outcome = final.get("outcome")
    if outcome == "failed":
        raise ExternalServiceError(final.get("reason") or "The AI service is unavailable.")
    if outcome != "suggested":
        raise InvalidInputError(final.get("reason") or "No questions could be suggested.")

    batch = _save(db, interview, final)
    log.info(
        "copilot_suggested",
        interview_id=interview.id,
        batch=batch,
        count=len(final["suggestions"]),
    )
    return {"interview_id": interview.id, "batch": batch, "count": len(final["suggestions"])}


def _save(db: Session, interview: Interview, final: dict[str, Any]) -> int:
    batch = (
        db.scalar(
            select(func.max(CopilotSuggestion.batch)).where(
                CopilotSuggestion.interview_id == interview.id
            )
        )
        or 0
    ) + 1
    targets = {t["requirement_id"]: t for t in final["targets"]}
    passages = {p["chunk_id"]: p for p in final.get("passages") or []}
    for s in final["suggestions"]:
        cited = list(dict.fromkeys(i for p in s["answer_points"] for i in p["passage_ids"]))
        db.add(
            CopilotSuggestion(
                interview_id=interview.id,
                job_requirement_id=s["requirement_id"],
                batch=batch,
                question_text=s["question"],
                rationale=s["why"] or None,
                reference_answer="\n".join(f"- {p['point']}" for p in s["answer_points"]) or None,
                sources=[
                    {
                        "chunk_id": i,
                        "title": passages[i]["title"],
                        "excerpt": passages[i]["text"][:600],
                    }
                    for i in cited
                ],
                details={
                    "basis": targets[s["requirement_id"]],
                    "evidence": s["evidence"],
                    "answer_points": s["answer_points"],
                    "focus": final.get("focus"),
                    "warnings": final.get("warnings") or [],
                },
                model_name=final.get("model_name"),
            )
        )
    db.commit()
    return batch


def suggestions(db: Session, interview_id: int) -> list[dict[str, Any]]:
    _interview(db, interview_id)
    rows = db.scalars(
        select(CopilotSuggestion)
        .where(CopilotSuggestion.interview_id == interview_id)
        .order_by(CopilotSuggestion.batch.desc(), CopilotSuggestion.id)
    ).all()
    req_ids = {r.job_requirement_id for r in rows if r.job_requirement_id}
    requirements = {
        r.id: r for r in db.scalars(select(JobRequirement).where(JobRequirement.id.in_(req_ids)))
    }
    out = []
    for r in rows:
        req = requirements.get(r.job_requirement_id)
        details = r.details or {}
        out.append(
            {
                "id": r.id,
                "batch": r.batch,
                "created_at": r.created_at,
                "requirement": (
                    {"id": req.id, "text": req.text, "must_have": req.is_mandatory} if req else None
                ),
                "question": r.question_text,
                "why": r.rationale,
                "basis": details.get("basis") or {},
                "evidence": details.get("evidence") or [],
                "answer_points": details.get("answer_points") or [],
                "sources": r.sources or [],
                "focus": details.get("focus"),
                "warnings": details.get("warnings") or [],
                "was_used": r.was_used,
                "model_name": r.model_name,
            }
        )
    return out


def mark_used(db: Session, interview_id: int, suggestion_id: int, used: bool) -> CopilotSuggestion:
    row = db.get(CopilotSuggestion, suggestion_id)
    if row is None or row.interview_id != interview_id:
        raise NotFoundError("That suggestion does not exist.")
    row.was_used = used
    db.commit()
    return row
