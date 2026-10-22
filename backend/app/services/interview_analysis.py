# the q&a analysis of a finished interview. ending the interview queues it; the worker
# waits for the transcriber to finish what it heard, runs the analysis graph and saves the
# question-answer pairs with their scores and requirement links. reading it calls no ai

import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from statistics import mean
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, selectinload

from app.agents.analysis.checks import SCORE_FIELDS
from app.agents.analysis.llm import PROMPT_VERSION
from app.agents.checkpointer import thread_config
from app.agents.runner import new_thread_id, run_graph
from app.errors import ConflictError, ExternalServiceError, InvalidInputError, NotFoundError
from app.logging_config import get_logger
from app.models.analysis import InterviewAnalysis, QaPair, QaRequirementLink
from app.models.candidate import Candidate
from app.models.enums import CoverageStatus, InterviewStatus
from app.models.interview import Interview, TranscriptTurn
from app.models.job import JobRequirement
from app.realtime import status as transcription
from app.services.jobs import get_job
from app.worker import queue

log = get_logger(__name__)

ProgressFn = Callable[..., None]
TRANSCRIPT_WAIT_SECONDS = 120
POLL_SECONDS = 3
# a run left "running" this long was lost with its worker
STALE_RUN = timedelta(minutes=15)

NEXT_STEP = {
    "segment": "Checking the question and answer pairs.",
    "check_segments": "Looking up reference material.",
    "use_exchanges": "Looking up reference material.",
    "retrieve": "Evaluating each answer.",
    "evaluate": "Checking the evaluations against the transcript.",
    "check_evaluations": "Mapping answers to the job's requirements.",
}


def _interview(db: Session, interview_id: int) -> Interview:
    interview = db.get(Interview, interview_id)
    if interview is None:
        raise NotFoundError("That interview does not exist.")
    return interview


def _row(db: Session, interview_id: int) -> InterviewAnalysis:
    row = db.scalar(select(InterviewAnalysis).where(InterviewAnalysis.interview_id == interview_id))
    if row is None:
        row = InterviewAnalysis(interview_id=interview_id)
        db.add(row)
    return row


def _aware(value: datetime | None) -> datetime | None:
    return value if value is None or value.tzinfo else value.replace(tzinfo=UTC)


# --- starting --------------------------------------------------------------------------


def _enqueue(db: Session, interview_id: int, wait_for_transcript: bool) -> str:
    row = _row(db, interview_id)
    row.status, row.message = "waiting", None
    db.commit()
    return queue.enqueue(
        "app.worker.tasks.analyse_interview_task",
        "interview_analysis",
        interview_id=interview_id,
        wait_for_transcript=wait_for_transcript,
    )


def queue_after_end(db: Session, interview_id: int) -> None:
    # never stops the interview from ending; a failure is shown on the analysis page
    try:
        _enqueue(db, interview_id, wait_for_transcript=True)
    except Exception:
        log.warning("analysis_not_queued", interview_id=interview_id)
        db.rollback()
        row = _row(db, interview_id)
        row.status = "failed"
        row.message = "The analysis couldn't be started. Press Analyse again."
        db.commit()


def start(db: Session, interview_id: int) -> str:
    interview = _interview(db, interview_id)
    if interview.status != InterviewStatus.COMPLETED:
        raise InvalidInputError("An interview is analysed once it has ended.")
    row = db.scalar(select(InterviewAnalysis).where(InterviewAnalysis.interview_id == interview_id))
    if (
        row is not None
        and row.status in ("waiting", "running")
        and _aware(row.updated_at) > datetime.now(UTC) - STALE_RUN
    ):
        raise ConflictError("This interview is already being analysed.")
    return _enqueue(db, interview_id, wait_for_transcript=False)


def wait_for_transcript(interview_id: int, timeout: float = TRANSCRIPT_WAIT_SECONDS) -> None:
    # the transcriber clears its room status once every utterance it heard is saved
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if transcription.read(interview_id)["state"] in ("stopped", "unknown"):
            return
        time.sleep(POLL_SECONDS)
    log.warning("analysis_transcript_wait_timed_out", interview_id=interview_id)


# --- running ---------------------------------------------------------------------------


def _turns(db: Session, interview_id: int) -> list[dict[str, Any]]:
    rows = db.scalars(
        select(TranscriptTurn)
        .where(TranscriptTurn.interview_id == interview_id)
        .order_by(TranscriptTurn.start_offset_ms, TranscriptTurn.sequence)
    )
    # gaps the speech services couldn't fill carry no words to analyse
    return [
        {"id": t.id, "speaker_role": t.speaker_role.value, "text": t.text}
        for t in rows
        if t.stt_provider != "none" and t.text.strip()
    ]


def analyse(
    db: Session,
    interview_id: int,
    graph: Any,
    progress: ProgressFn | None = None,
    wait: bool = True,
) -> dict[str, Any]:
    def report(message: str) -> None:
        if progress:
            progress(state="running", message=message, total=0, completed=0)

    interview = _interview(db, interview_id)
    if interview.status != InterviewStatus.COMPLETED:
        raise InvalidInputError("An interview is analysed once it has ended.")
    row = _row(db, interview_id)
    row.status, row.message = "running", None
    db.commit()

    if wait:
        report("Waiting for the last transcript lines.")
        wait_for_transcript(interview_id)

    job = get_job(db, interview.job_id)
    turns = _turns(db, interview_id)
    thread_id = new_thread_id("analysis")
    initial = {
        "thread_id": thread_id,
        "interview_id": interview_id,
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
        "turns": turns,
        "errors": [],
    }

    def invoke() -> dict[str, Any]:
        final: dict[str, Any] = {}
        report("Splitting the transcript into questions and answers.")
        for mode, chunk in graph.stream(
            initial, config=thread_config(thread_id), stream_mode=["updates", "values"]
        ):
            if mode == "values":
                final = chunk
                continue
            for node in chunk:
                if node in NEXT_STEP:
                    report(NEXT_STEP[node])
        return final

    try:
        final = run_graph(
            db,
            graph_name="interview_analysis",
            thread_id=thread_id,
            invoke=invoke,
            # the transcript is the candidate's data, so deletion must find this thread
            context={
                "interview_id": interview_id,
                "candidate_id": interview.candidate_id,
                "job_id": interview.job_id,
                "prompt_version": PROMPT_VERSION,
            },
            failure_code=lambda s: None if s.get("outcome") in ("analysed", "empty") else "failed",
        )
    except ExternalServiceError as exc:
        _finish(db, row, "failed", exc.message)
        raise

    outcome = final.get("outcome")
    if outcome == "empty":
        _clear(db, interview_id)
        _finish(db, row, "empty", final.get("reason"), turn_count=len(turns))
        return {"interview_id": interview_id, "pairs": 0}
    if outcome != "analysed":
        message = final.get("reason") or "The analysis could not be completed."
        _finish(db, row, "failed", message)
        raise ExternalServiceError(message)

    count = _save(db, interview_id, final)
    row.segmented_by = final.get("segmented_by")
    row.follow_ups = final.get("follow_ups") or []
    row.warnings = sorted(set(final.get("warnings") or []))
    row.model_name = final.get("model_name")
    row.prompt_version = PROMPT_VERSION
    row.analysed_at = datetime.now(UTC)
    _finish(db, row, "analysed", None, turn_count=len(turns))
    log.info("interview_analysed", interview_id=interview_id, pairs=count)
    from app.services import assessment

    assessment.queue_after_analysis(db, interview_id)
    return {"interview_id": interview_id, "pairs": count}


def _finish(
    db: Session,
    row: InterviewAnalysis,
    status: str,
    message: str | None,
    turn_count: int | None = None,
) -> None:
    row.status, row.message = status, message[:500] if message else None
    if turn_count is not None:
        row.turn_count = turn_count
    db.commit()


def _clear(db: Session, interview_id: int) -> None:
    ids = db.scalars(select(QaPair.id).where(QaPair.interview_id == interview_id)).all()
    if ids:
        db.execute(delete(QaRequirementLink).where(QaRequirementLink.qa_pair_id.in_(ids)))
        db.execute(delete(QaPair).where(QaPair.id.in_(ids)))
    db.flush()


def _save(db: Session, interview_id: int, final: dict[str, Any]) -> int:
    # a re-analysis replaces the earlier pairs
    _clear(db, interview_id)
    by_id = {t["id"]: t["text"] for t in final["turns"]}
    passages = {f"S{i}": p for i, p in enumerate(final.get("passages") or [], start=1)}
    evaluations = final.get("evaluations") or {}
    rows: list[QaPair] = []
    for index, pair in enumerate(final["pairs"]):
        ev = evaluations.get(str(index)) or {}
        justification = ev.get("justification") or None
        cited = [label for label in passages if f"[{label}]" in (justification or "")]
        row = QaPair(
            interview_id=interview_id,
            sequence=index + 1,
            question_text=pair["question"],
            answer_text=" ".join(by_id.get(i, "") for i in pair["answer_turn_ids"]).strip(),
            question_turn_ids=pair["question_turn_ids"],
            answer_turn_ids=pair["answer_turn_ids"],
            relevance_score=ev.get("relevance"),
            accuracy_score=ev.get("accuracy"),
            completeness_score=ev.get("completeness"),
            articulation_score=ev.get("articulation"),
            accuracy_basis=ev.get("accuracy_basis"),
            justification=justification,
            sources=[
                {
                    "label": label,
                    "chunk_id": passages[label]["chunk_id"],
                    "title": passages[label]["title"],
                    "excerpt": passages[label]["text"][:600],
                }
                for label in cited
            ],
        )
        row.requirement_links = [
            QaRequirementLink(
                job_requirement_id=link["requirement_id"],
                coverage=CoverageStatus(link["coverage"]),
                note=link["note"] or None,
            )
            for link in ev.get("requirements") or []
        ]
        db.add(row)
        rows.append(row)
    db.flush()
    for index, pair in enumerate(final["pairs"]):
        follow = pair.get("follow_up_of")
        if follow is not None and follow < index:
            rows[index].follow_up_to_id = rows[follow].id
    db.commit()
    return len(rows)


# --- reading ---------------------------------------------------------------------------


def pair_score(p: QaPair) -> float | None:
    # one number per answer: the mean of the dimensions that could be judged
    values = [
        v
        for v in (p.relevance_score, p.accuracy_score, p.completeness_score, p.articulation_score)
        if v is not None
    ]
    return round(mean(values), 1) if values else None


def averages(pairs: list[QaPair]) -> dict[str, float | None]:
    out: dict[str, float | None] = {}
    for f in SCORE_FIELDS:
        values = [v for p in pairs if (v := getattr(p, f"{f}_score")) is not None]
        out[f] = round(mean(values), 1) if values else None
    return out


def coverage(requirements: list[JobRequirement], pairs: list[QaPair]) -> list[dict[str, Any]]:
    out = []
    for r in requirements:
        links = [
            (p, link)
            for p in pairs
            for link in p.requirement_links
            if link.job_requirement_id == r.id
        ]
        statuses = {link.coverage for _, link in links}
        status = (
            CoverageStatus.COVERED
            if CoverageStatus.COVERED in statuses
            else CoverageStatus.PARTIALLY_COVERED
            if statuses
            else CoverageStatus.NOT_EXPLORED
        )
        out.append(
            {
                "requirement_id": r.id,
                "text": r.text,
                "must_have": r.is_mandatory,
                "status": status.value,
                "pairs": [
                    {"sequence": p.sequence, "coverage": link.coverage.value, "note": link.note}
                    for p, link in links
                ],
            }
        )
    return out


def _pairs(db: Session, interview_id: int) -> list[QaPair]:
    return list(
        db.scalars(
            select(QaPair)
            .where(QaPair.interview_id == interview_id)
            .options(selectinload(QaPair.requirement_links))
            .order_by(QaPair.sequence)
        )
    )


def results(db: Session, interview_id: int) -> dict[str, Any]:
    interview = _interview(db, interview_id)
    job = get_job(db, interview.job_id)
    candidate = db.get(Candidate, interview.candidate_id)
    row = db.scalar(select(InterviewAnalysis).where(InterviewAnalysis.interview_id == interview_id))
    pairs = _pairs(db, interview_id)
    requirements = {r.id: r for r in job.requirements}
    sequence_of = {p.id: p.sequence for p in pairs}
    turn_ids = {i for p in pairs for i in (p.question_turn_ids or []) + (p.answer_turn_ids or [])}
    turns = (
        db.scalars(select(TranscriptTurn).where(TranscriptTurn.id.in_(turn_ids)))
        if turn_ids
        else []
    )
    turn_count = db.scalar(
        select(func.count())
        .select_from(TranscriptTurn)
        .where(TranscriptTurn.interview_id == interview_id)
    )
    scored = [s for s in (pair_score(p) for p in pairs) if s is not None]
    covered = coverage(list(job.requirements), pairs)
    return {
        "interview": {
            "id": interview.id,
            "job_id": job.id,
            "job_title": job.title,
            "candidate_id": interview.candidate_id,
            "candidate_name": candidate.full_name if candidate else "",
            "status": interview.status.value,
            "ended_at": _aware(interview.ended_at),
            "timezone_name": interview.timezone_name,
        },
        "analysis": (
            {
                "status": row.status,
                "message": row.message,
                "segmented_by": row.segmented_by,
                "analysed_at": _aware(row.analysed_at),
                "model_name": row.model_name,
                "warnings": row.warnings or [],
                # a line saved after the analysis means it didn't see everything
                "stale": row.status == "analysed" and (turn_count or 0) > row.turn_count,
            }
            if row
            else None
        ),
        "summary": {
            "pairs": len(pairs),
            "answer_quality": round(mean(scored), 1) if scored else None,
            "averages": averages(pairs),
            "covered": sum(1 for c in covered if c["status"] == "covered"),
            "partially_covered": sum(1 for c in covered if c["status"] == "partially_covered"),
            "not_explored": sum(1 for c in covered if c["status"] == "not_explored"),
        },
        "pairs": [
            {
                "id": p.id,
                "sequence": p.sequence,
                "question": p.question_text,
                "answer": p.answer_text,
                "question_turn_ids": p.question_turn_ids or [],
                "answer_turn_ids": p.answer_turn_ids or [],
                "follow_up_to": sequence_of.get(p.follow_up_to_id),
                "scores": {f: getattr(p, f"{f}_score") for f in SCORE_FIELDS},
                "score": pair_score(p),
                "accuracy_basis": p.accuracy_basis,
                "justification": p.justification,
                "sources": p.sources or [],
                "requirements": [
                    {
                        "requirement_id": link.job_requirement_id,
                        "text": requirements[link.job_requirement_id].text
                        if link.job_requirement_id in requirements
                        else "",
                        "coverage": link.coverage.value,
                        "note": link.note,
                    }
                    for link in p.requirement_links
                ],
            }
            for p in pairs
        ],
        "turns": [
            {
                "id": t.id,
                "speaker_role": t.speaker_role.value,
                "text": t.text,
                "start_offset_ms": t.start_offset_ms,
            }
            for t in sorted(turns, key=lambda t: (t.start_offset_ms, t.sequence))
        ],
        "coverage": covered,
        "follow_ups": [
            {
                **f,
                "requirement_text": requirements[f["requirement_id"]].text
                if f["requirement_id"] in requirements
                else "",
            }
            for f in (row.follow_ups or [] if row else [])
        ],
    }
