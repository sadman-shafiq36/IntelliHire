# the consolidated assessment of an interviewed candidate: the cv screening, the score and
# the interview analysis in one report. it runs after the analysis; the numbers and the
# position are code, the text is checked ai output. reading, exporting and comparing call no ai

import html
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.agents.assessment.checks import KINDS, WEIGHTS
from app.agents.assessment.llm import LABELS, PROMPT_VERSION
from app.agents.checkpointer import thread_config
from app.agents.runner import new_thread_id, run_graph
from app.errors import ConflictError, ExternalServiceError, InvalidInputError, NotFoundError
from app.logging_config import get_logger
from app.models.analysis import Assessment, AssessmentFinding, InterviewAnalysis
from app.models.candidate import Candidate, CvExtraction
from app.models.enums import EvidenceType, FindingKind, InterviewStatus
from app.models.interview import Interview, TranscriptTurn
from app.models.screening import CandidateScore, Screening
from app.services import interview_analysis
from app.services.jobs import get_job
from app.worker import queue

log = get_logger(__name__)

ProgressFn = Callable[..., None]
STALE_RUN = timedelta(minutes=15)

COMPONENT_LABEL = {
    "cv_fit": "CV fit",
    "interview_answers": "Interview answers",
    "requirement_coverage": "Requirement coverage",
}
KIND_LABEL = {
    "strength": "Strengths",
    "weakness": "Weaknesses",
    "evidence_gap": "Evidence gaps",
    "area_to_verify": "Areas to verify",
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


def _row(db: Session, interview: Interview) -> Assessment:
    row = db.scalar(select(Assessment).where(Assessment.interview_id == interview.id))
    if row is None:
        row = Assessment(
            interview_id=interview.id, job_id=interview.job_id, candidate_id=interview.candidate_id
        )
        db.add(row)
    return row


# --- starting --------------------------------------------------------------------------


def _enqueue(db: Session, interview: Interview) -> str:
    row = _row(db, interview)
    row.status, row.message = "waiting", None
    db.commit()
    return queue.enqueue(
        "app.worker.tasks.assess_candidate_task",
        "candidate_assessment",
        interview_id=interview.id,
    )


def queue_after_analysis(db: Session, interview_id: int) -> None:
    # a new analysis means a new report; failing to queue it never fails the analysis
    try:
        _enqueue(db, _interview(db, interview_id))
    except Exception:
        log.warning("assessment_not_queued", interview_id=interview_id)
        db.rollback()


def _check_ready(db: Session, interview: Interview) -> None:
    if interview.status != InterviewStatus.COMPLETED:
        raise InvalidInputError("A candidate is assessed once their interview has ended.")
    analysis = _analysis(db, interview.id)
    if analysis is None or analysis.status != "analysed":
        raise InvalidInputError(
            "The interview has to be analysed first. Open its analysis and press Analyse."
        )


def start(db: Session, interview_id: int) -> str:
    interview = _interview(db, interview_id)
    _check_ready(db, interview)
    row = db.scalar(select(Assessment).where(Assessment.interview_id == interview_id))
    if (
        row is not None
        and row.status in ("waiting", "running")
        and _aware(row.updated_at) > datetime.now(UTC) - STALE_RUN
    ):
        raise ConflictError("This report is already being written.")
    return _enqueue(db, interview)


# --- running ---------------------------------------------------------------------------


def build_state(db: Session, interview: Interview) -> dict[str, Any]:
    job = get_job(db, interview.job_id)
    screening = db.scalar(
        select(Screening).where(
            Screening.job_id == job.id, Screening.candidate_id == interview.candidate_id
        )
    )
    results = {r.job_requirement_id: r for r in screening.requirement_results} if screening else {}
    cv_text, cv_document_id = "", None
    if screening and screening.cv_document_id:
        cv_document_id = screening.cv_document_id
        extraction = db.scalar(
            select(CvExtraction).where(CvExtraction.cv_document_id == screening.cv_document_id)
        )
        # stored with name, email and phone already removed
        cv_text = (extraction.raw_text or "") if extraction else ""
    score = db.scalar(
        select(CandidateScore).where(
            CandidateScore.job_id == job.id, CandidateScore.candidate_id == interview.candidate_id
        )
    )
    pairs = interview_analysis._pairs(db, interview.id)
    covered = {
        c["requirement_id"]: c["status"]
        for c in interview_analysis.coverage(list(job.requirements), pairs)
    }
    scored = [s for s in (interview_analysis.pair_score(p) for p in pairs) if s is not None]
    turn_ids = {i for p in pairs for i in (p.question_turn_ids or []) + (p.answer_turn_ids or [])}
    turns = (
        db.scalars(select(TranscriptTurn).where(TranscriptTurn.id.in_(turn_ids))).all()
        if turn_ids
        else []
    )
    return {
        "interview_id": interview.id,
        "job": {"title": job.title},
        "requirements": [
            {
                "id": r.id,
                "text": r.text,
                "is_mandatory": r.is_mandatory,
                "weight": r.weight,
                "cv_status": results[r.id].status.value if r.id in results else None,
                "cv_quote": (results[r.id].evidence_quote or None) if r.id in results else None,
                "interview_status": covered.get(r.id, "not_explored"),
            }
            for r in job.requirements
        ],
        "cv_score": score.aggregate_score if score else None,
        "answer_quality": round(sum(scored) / len(scored), 1) if scored else None,
        "pairs": [
            {
                "sequence": p.sequence,
                "question": p.question_text,
                "answer_turn_ids": p.answer_turn_ids or [],
                "score": interview_analysis.pair_score(p),
                "justification": p.justification,
            }
            for p in pairs
        ],
        "turns": [
            {"id": t.id, "speaker_role": t.speaker_role.value, "text": t.text} for t in turns
        ],
        "cv_text": cv_text,
        "cv_document_id": cv_document_id,
    }


def assess(
    db: Session, interview_id: int, graph: Any, progress: ProgressFn | None = None
) -> dict[str, Any]:
    def say(message: str) -> None:
        if progress:
            progress(state="running", message=message, total=0, completed=0)

    interview = _interview(db, interview_id)
    _check_ready(db, interview)
    analysis = _analysis(db, interview_id)
    row = _row(db, interview)
    row.status, row.message = "running", None
    db.commit()

    thread_id = new_thread_id("assessment")
    initial = {"thread_id": thread_id, **build_state(db, interview), "errors": []}

    def invoke() -> dict[str, Any]:
        final: dict[str, Any] = {}
        say("Combining the CV score, the interview and the requirement evidence.")
        for mode, chunk in graph.stream(
            initial, config=thread_config(thread_id), stream_mode=["updates", "values"]
        ):
            if mode == "values":
                final = chunk
                continue
            for node in chunk:
                if node == "compute":
                    say("Writing strengths, weaknesses and gaps.")
                elif node == "write":
                    say("Checking every finding against the CV and the transcript.")
        return final

    try:
        final = run_graph(
            db,
            graph_name="candidate_assessment",
            thread_id=thread_id,
            invoke=invoke,
            # the graph reads the cv and the transcript, so deletion must find this thread
            context={
                "interview_id": interview_id,
                "candidate_id": interview.candidate_id,
                "cv_document_id": initial["cv_document_id"],
                "job_id": interview.job_id,
                "prompt_version": PROMPT_VERSION,
            },
            failure_code=lambda s: None if s.get("outcome") == "assessed" else "failed",
        )
    except ExternalServiceError as exc:
        row.status, row.message = "failed", exc.message[:500]
        db.commit()
        raise

    if final.get("outcome") != "assessed":
        message = final.get("reason") or "The assessment could not be completed."
        row.status, row.message = "failed", message[:500]
        db.commit()
        raise InvalidInputError(message)

    row.overall_score = final["overall"]
    row.overall_position = final["position"]
    row.component_scores = {
        **final["components"],
        "requirements": final["requirements"],
        "capped_by": final.get("capped_by") or [],
        "cv_score": initial["cv_score"],
        "answer_quality": initial["answer_quality"],
    }
    row.summary = final.get("summary") or None
    row.warnings = final.get("warnings") or []
    row.model_name = final.get("model_name") or "none"
    row.prompt_version = PROMPT_VERSION
    row.generated_at = datetime.now(UTC)
    row.based_on_analysed_at = analysis.analysed_at if analysis else None
    row.findings = [
        AssessmentFinding(
            kind=FindingKind(f["kind"]),
            text=f["text"],
            evidence_type=EvidenceType(f["evidence"][0]["type"]),
            evidence_ref_id=f["evidence"][0]["ref_id"],
            evidence_quote=f["evidence"][0]["quote"],
            evidence=f["evidence"],
        )
        for f in final.get("findings") or []
    ]
    row.status, row.message = "assessed", None
    db.commit()
    log.info(
        "candidate_assessed",
        interview_id=interview_id,
        position=row.overall_position,
        findings=len(row.findings),
    )
    return {"interview_id": interview_id, "position": row.overall_position}


# --- reading ---------------------------------------------------------------------------


def _turn_info(db: Session, ids: set[int]) -> dict[int, dict[str, Any]]:
    if not ids:
        return {}
    return {
        t.id: {"speaker_role": t.speaker_role.value, "start_offset_ms": t.start_offset_ms}
        for t in db.scalars(select(TranscriptTurn).where(TranscriptTurn.id.in_(ids)))
    }


def report(db: Session, interview_id: int) -> dict[str, Any]:
    interview = _interview(db, interview_id)
    job = get_job(db, interview.job_id)
    candidate = db.get(Candidate, interview.candidate_id)
    analysis = _analysis(db, interview_id)
    row = db.scalar(
        select(Assessment)
        .where(Assessment.interview_id == interview_id)
        .options(selectinload(Assessment.findings))
    )
    base = {
        "interview": {
            "id": interview.id,
            "job_id": job.id,
            "job_title": job.title,
            "candidate_id": interview.candidate_id,
            "candidate_name": candidate.full_name if candidate else "",
            "status": interview.status.value,
            "started_at": _aware(interview.started_at),
            "ended_at": _aware(interview.ended_at),
            "timezone_name": interview.timezone_name,
        },
        "analysis_status": analysis.status if analysis else None,
        "assessment": None,
    }
    if row is None:
        return base
    turn_ids = {
        e["ref_id"] for f in row.findings for e in f.evidence or [] if e["type"] == "transcript"
    }
    turns = _turn_info(db, turn_ids)
    components = row.component_scores or {}
    base["assessment"] = {
        "status": row.status,
        "message": row.message,
        "overall_score": row.overall_score,
        "position": row.overall_position,
        "position_label": LABELS.get(row.overall_position or "", ""),
        "components": [
            {"name": k, "label": COMPONENT_LABEL[k], **components[k]}
            for k in WEIGHTS
            if k in components
        ],
        "requirements": components.get("requirements") or [],
        "capped_by": components.get("capped_by") or [],
        "summary": row.summary,
        "findings": [
            {
                "kind": f.kind.value,
                "text": f.text,
                "evidence": [
                    {**e, **(turns.get(e["ref_id"], {}) if e["type"] == "transcript" else {})}
                    for e in f.evidence or []
                ],
            }
            for f in sorted(row.findings, key=lambda f: (KINDS.index(f.kind.value), f.id or 0))
        ],
        "warnings": row.warnings or [],
        "model_name": row.model_name,
        "generated_at": _aware(row.generated_at),
        # the analysis was redone after this report was written
        "stale": bool(
            analysis
            and analysis.analysed_at
            and row.based_on_analysed_at
            and _aware(analysis.analysed_at) > _aware(row.based_on_analysed_at)
        ),
    }
    return base


def compare(db: Session, job_id: int) -> dict[str, Any]:
    # every assessed candidate for the job, latest interview each, best overall first
    job = get_job(db, job_id)
    rows = db.scalars(
        select(Assessment)
        .where(Assessment.job_id == job_id, Assessment.status == "assessed")
        .options(selectinload(Assessment.findings))
        .order_by(Assessment.generated_at.desc())
    ).all()
    latest: dict[int, Assessment] = {}
    for r in rows:
        latest.setdefault(r.candidate_id, r)
    names = {
        c.id: c.full_name
        for c in db.scalars(select(Candidate).where(Candidate.id.in_(list(latest))))
    }
    out = []
    for r in latest.values():
        components = r.component_scores or {}
        requirements = components.get("requirements") or []
        out.append(
            {
                "interview_id": r.interview_id,
                "candidate_id": r.candidate_id,
                "full_name": names.get(r.candidate_id, ""),
                "overall_score": r.overall_score,
                "position": r.overall_position,
                "position_label": LABELS.get(r.overall_position or "", ""),
                "components": {k: (components.get(k) or {}).get("score") for k in WEIGHTS},
                "requirements": {
                    str(q["id"]): {
                        "cv": q.get("cv_status"),
                        "interview": q.get("interview_status"),
                    }
                    for q in requirements
                },
                "findings": {
                    kind: [f.text for f in r.findings if f.kind.value == kind] for kind in KINDS
                },
                "generated_at": _aware(r.generated_at),
            }
        )
    out.sort(key=lambda c: (-(c["overall_score"] or 0), c["full_name"]))
    return {
        "job_id": job.id,
        "job_title": job.title,
        "weights": WEIGHTS,
        "requirements": [
            {"id": r.id, "text": r.text, "must_have": r.is_mandatory} for r in job.requirements
        ],
        "candidates": out,
    }


# --- export ----------------------------------------------------------------------------


def _e(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def _when(value: datetime | None, tz: str) -> str:
    if value is None:
        return ""
    from zoneinfo import ZoneInfo

    return value.astimezone(ZoneInfo(tz)).strftime("%d %b %Y, %H:%M")


def _offset(ms: int | None) -> str:
    seconds = (ms or 0) // 1000
    return f"{seconds // 60}:{seconds % 60:02d}"


def render_html(data: dict[str, Any]) -> str:
    a = data["assessment"]
    if a is None or a["status"] != "assessed":
        raise InvalidInputError("There is no finished report for this interview yet.")
    iv = data["interview"]
    components = "".join(
        f"<tr><td>{_e(c['label'])}</td><td>{_e(c['score'] if c['score'] is not None else '–')}</td>"
        f"<td>{_e(c['effective_weight'])}%</td><td>{_e(c['contribution'])}</td></tr>"
        for c in a["components"]
    )
    cv_label = {"met": "Met", "partially_met": "Partly met", "unmet": "Not met", None: "–"}
    iv_label = {
        "covered": "Covered",
        "partially_covered": "Partly covered",
        "not_explored": "Not explored",
    }
    requirements = "".join(
        f"<tr><td>{_e(r['text'])}{' <small>(must-have)</small>' if r['is_mandatory'] else ''}</td>"
        f"<td>{cv_label.get(r.get('cv_status'), '–')}</td>"
        f"<td>{iv_label.get(r.get('interview_status'), '–')}</td></tr>"
        for r in a["requirements"]
    )

    def evidence(e: dict[str, Any]) -> str:
        if e["type"] == "cv":
            where = "CV"
        elif e["type"] == "transcript":
            who = "Candidate" if e.get("speaker_role") == "candidate" else "Interviewer"
            where = f"{who}, {_offset(e.get('start_offset_ms'))} into the interview"
        else:
            where = "Requirement"
        return f'<li><span class="where">{where}:</span> <mark>{_e(e["quote"])}</mark></li>'

    sections = ""
    for kind in KINDS:
        items = [f for f in a["findings"] if f["kind"] == kind]
        if not items:
            continue
        sections += f"<h2>{KIND_LABEL[kind]}</h2>" + "".join(
            f"<div class='finding'><p>{_e(f['text'])}</p><ul>"
            + "".join(evidence(e) for e in f["evidence"])
            + "</ul></div>"
            for f in items
        )
    capped = (
        "<p class='note'>Not a strong fit while these must-haves have no evidence: "
        + _e("; ".join(a["capped_by"]))
        + "</p>"
        if a["capped_by"]
        else ""
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Assessment: {_e(iv["candidate_name"])}, {_e(iv["job_title"])}</title>
<style>
body {{ font-family: "Schibsted Grotesk", Arial, sans-serif; color: #17211f; margin: 32px auto;
  max-width: 760px; line-height: 1.45; font-size: 14px; }}
h1 {{ font-size: 24px; margin: 0 0 4px; }} h2 {{ font-size: 16px; margin: 24px 0 8px;
  border-bottom: 1px solid #d9dcd2; padding-bottom: 4px; }}
.meta, .note, small, .where {{ color: #61685c; }}
.position {{ display: inline-block; padding: 4px 10px; border-radius: 999px; background: #cde6dd;
  color: #0a4538; font-weight: 600; }}
table {{ border-collapse: collapse; width: 100%; }} td, th {{ text-align: left; padding: 4px 8px;
  border-bottom: 1px solid #eaece6; vertical-align: top; }}
.finding {{ margin: 0 0 12px; }} .finding p {{ margin: 0 0 4px; }}
.finding ul {{ margin: 0; padding-left: 18px; }} mark {{ background: #f7f1a6; }}
@page {{ margin: 18mm; }}
</style></head><body>
<h1>{_e(iv["candidate_name"])}</h1>
<p class="meta">{_e(iv["job_title"])} · interview {_e(_when(iv["started_at"], iv["timezone_name"]))}
({_e(iv["timezone_name"])}) · report {_e(_when(a["generated_at"], iv["timezone_name"]))}</p>
<p><span class="position">{_e(a["position_label"].capitalize())}</span>
&nbsp;<strong>{_e(a["overall_score"])}</strong> of 100</p>
{capped}
<p>{_e(a["summary"] or "")}</p>
<h2>How the overall score is made up</h2>
<table><tr><th>Component</th><th>Score</th><th>Weight</th><th>Points</th></tr>{components}</table>
<h2>Requirements</h2>
<table><tr><th>Requirement</th><th>CV screening</th><th>Interview</th></tr>{requirements}</table>
{sections}
<p class="note">Every finding quotes the CV or the interview transcript it rests on. The
position is worked out from the scores; the decision is the hiring team's.
Written by {_e(a["model_name"])}.</p>
</body></html>"""


def render_pdf(data: dict[str, Any]) -> bytes:
    from weasyprint import HTML

    return HTML(string=render_html(data)).write_pdf()
