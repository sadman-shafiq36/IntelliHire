# cv intake by upload, and screening a job's cvs: each cv runs through the screening
# graph on a worker thread, results are saved one by one on this thread, then ranked

import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.agents.checkpointer import thread_config
from app.agents.runner import new_thread_id, record_run
from app.agents.screening.llm import PROMPT_VERSION
from app.config import settings
from app.errors import InvalidInputError, NotFoundError
from app.logging_config import get_logger
from app.models.candidate import Application, Candidate, CvDocument, CvExtraction
from app.models.enums import ExtractionStatus, RequirementMatchStatus, ScreeningStatus
from app.models.job import Job
from app.models.screening import Screening, ScreeningRequirementResult
from app.services.ai import seconds_until_available
from app.services.jobs import get_job

log = get_logger(__name__)

ProgressFn = Callable[..., None]
MAX_FILES_PER_UPLOAD = 50
MAX_RETRY_WAIT = 90.0


# --- upload -----------------------------------------------------------------------


def upload_cvs(
    db: Session, job_id: int, files: list[tuple[str, str, bytes]], user_id: int
) -> dict[str, Any]:
    get_job(db, job_id)
    if not files:
        raise InvalidInputError("Choose at least one CV to upload.")
    if len(files) > MAX_FILES_PER_UPLOAD:
        raise InvalidInputError(f"Upload at most {MAX_FILES_PER_UPLOAD} CVs at a time.")

    folder = Path(settings.storage_dir) / "cvs"
    folder.mkdir(parents=True, exist_ok=True)
    accepted, rejected = [], []
    for filename, content_type, data in files:
        name = Path(filename or "cv").name[:300]
        suffix = Path(name).suffix.lower()
        # each file is checked on its own, a bad one never blocks the rest
        if suffix not in settings.allowed_cv_extension_list:
            rejected.append({"filename": name, "reason": "Only PDF and Word (.docx) files."})
            continue
        if not data:
            rejected.append({"filename": name, "reason": "The file is empty."})
            continue
        if len(data) > settings.max_upload_bytes:
            rejected.append(
                {"filename": name, "reason": f"Larger than {settings.max_upload_mb} MB."}
            )
            continue
        stored = folder / f"{uuid.uuid4().hex}{suffix}"
        stored.write_bytes(data)
        doc = CvDocument(
            job_id=job_id,
            candidate_id=None,
            original_filename=name,
            storage_path=str(stored),
            content_type=content_type[:120] or "application/octet-stream",
            size_bytes=len(data),
            status=ExtractionStatus.PENDING,
            source="upload",
            uploaded_by_id=user_id,
        )
        db.add(doc)
        db.flush()
        accepted.append({"id": doc.id, "filename": name})
    db.commit()
    log.info("cvs_uploaded", job_id=job_id, accepted=len(accepted), rejected=len(rejected))
    return {"accepted": accepted, "rejected": rejected}


def list_documents(db: Session, job_id: int) -> list[dict[str, Any]]:
    get_job(db, job_id)
    docs = db.scalars(
        select(CvDocument).where(CvDocument.job_id == job_id).order_by(CvDocument.id.desc())
    )
    out = []
    for d in docs:
        candidate = db.get(Candidate, d.candidate_id) if d.candidate_id else None
        out.append(
            {
                "id": d.id,
                "filename": d.original_filename,
                "source": d.source,
                "status": d.status.value,
                "failure_reason": d.failure_reason,
                "used_ocr": d.used_ocr,
                "candidate_name": candidate.full_name if candidate else None,
                "uploaded_at": d.created_at,
            }
        )
    return out


def document_file(db: Session, doc_id: int) -> CvDocument:
    doc = db.get(CvDocument, doc_id)
    if doc is None or not Path(doc.storage_path).exists():
        raise NotFoundError("That CV is not available.")
    return doc


def pending_count(db: Session, job_id: int) -> int:
    return db.scalar(
        select(func.count())
        .select_from(CvDocument)
        .where(CvDocument.job_id == job_id, CvDocument.status.in_(ExtractionStatus.waiting()))
    )


# --- screening ----------------------------------------------------------------------


def release_unfinished(db: Session, job_id: int, doc_ids: list[int] | None = None) -> int:
    query = select(CvDocument).where(
        CvDocument.job_id == job_id, CvDocument.status == ExtractionStatus.EXTRACTING
    )
    if doc_ids is not None:
        query = query.where(CvDocument.id.in_(doc_ids))
    stuck = db.scalars(query).all()
    for d in stuck:
        d.status = ExtractionStatus.PENDING
    db.commit()
    if stuck:
        log.info("screening_released_unfinished", job_id=job_id, count=len(stuck))
    return len(stuck)


def start_screening(db: Session, job_id: int, rescreen: bool) -> int:
    job = get_job(db, job_id)
    # a batch that died (worker restart, crash) left its cvs marked as being screened.
    # two batches for one job at once is not possible, the queue runs one job at a time
    release_unfinished(db, job_id)
    if not job.requirements:
        raise InvalidInputError("Add requirements to the job before screening CVs.")
    # failed means the ai service was busy, so those always go again. with rescreen,
    # everything readable goes again too, e.g. after the requirements changed
    again = [ExtractionStatus.FAILED]
    if rescreen:
        again.append(ExtractionStatus.EXTRACTED)
    docs = db.scalars(
        select(CvDocument).where(CvDocument.job_id == job_id, CvDocument.status.in_(again))
    ).all()
    for d in docs:
        d.status = ExtractionStatus.PENDING
    db.commit()
    count = pending_count(db, job_id)
    if count == 0:
        raise InvalidInputError("There are no new CVs to screen. Upload or sync some first.")
    return count


def screen_job(
    db: Session,
    job_id: int,
    graph: Any,
    progress: ProgressFn | None = None,
    concurrency: int | None = None,
) -> dict[str, Any]:
    job = get_job(db, job_id)
    requirements = [
        {
            "id": r.id,
            "text": r.text,
            "category": r.category.value,
            "is_mandatory": r.is_mandatory,
            "weight": r.weight,
        }
        for r in job.requirements
    ]
    job_info = {"title": job.title, "min_years_experience": job.min_years_experience}
    docs = db.scalars(
        select(CvDocument).where(
            CvDocument.job_id == job_id, CvDocument.status == ExtractionStatus.PENDING
        )
    ).all()
    work = [(d.id, d.storage_path) for d in docs]
    for d in docs:
        d.status = ExtractionStatus.EXTRACTING
    db.commit()

    total = len(work)
    done = 0
    counts = {"screened": 0, "excluded": 0, "unreadable": 0, "not_english": 0, "failed": 0}
    failed_items: list[dict[str, Any]] = []
    started_batch = time.monotonic()

    def report(message: str) -> None:
        if progress:
            progress(
                state="running",
                message=message,
                total=total,
                completed=done,
                failed_items=failed_items,
            )

    def run_one(
        doc_id: int, path: str
    ) -> tuple[int, dict[str, Any], str, datetime, int, str | None]:
        thread_id = new_thread_id("screen")
        started = datetime.now(UTC)
        t0 = time.monotonic()
        error_code = None
        try:
            final = graph.invoke(
                {
                    "thread_id": thread_id,
                    "cv_document_id": doc_id,
                    "file_path": path,
                    "job": job_info,
                    "requirements": requirements,
                    "errors": [],
                },
                config=thread_config(thread_id),
            )
        except Exception as exc:
            # a crash in one cv is reported for that cv and never stops the batch
            log.exception("screening_crashed", cv_document_id=doc_id, error_type=type(exc).__name__)
            final = {
                "outcome": "failed",
                "reason": "Something went wrong while screening this CV. Try screening it again.",
            }
            error_code = type(exc).__name__
        if final.get("outcome") == "failed" and not error_code:
            error_code = "llm_failed"
        return doc_id, final, thread_id, started, int((time.monotonic() - t0) * 1000), error_code

    report(f"Screening {total} CV{'s' if total != 1 else ''}.")
    workers = max(1, concurrency or settings.screening_concurrency)
    paths = dict(work)

    def handle(doc_id, final, thread_id, started, duration_ms, error_code) -> None:
        nonlocal done
        outcome = final.get("outcome", "failed")
        _save(db, job, doc_id, final, requirements)
        record_run(
            db,
            "cv_screening",
            thread_id,
            started,
            duration_ms,
            error_code,
            {"job_id": job_id, "cv_document_id": doc_id, "outcome": outcome},
        )
        db.commit()
        counts[outcome] = counts.get(outcome, 0) + 1
        done += 1
        if outcome not in ("screened", "excluded"):
            failed_items.append(
                {"item_ref": str(doc_id), "reason": final.get("reason") or "Not screened."}
            )
        report(f"Screened {done} of {total}.")

    def run_batch(batch: list[tuple[int, str]], retry_failed: bool) -> list[int]:
        retry: list[int] = []
        pool = ThreadPoolExecutor(max_workers=workers)
        try:
            futures = [pool.submit(run_one, doc_id, path) for doc_id, path in batch]
            for future in as_completed(futures):
                doc_id, final, thread_id, started, duration_ms, error_code = future.result()
                # an ai hiccup gets one more go at the end instead of being left failed
                if retry_failed and final.get("outcome") == "failed":
                    record_run(
                        db,
                        "cv_screening",
                        thread_id,
                        started,
                        duration_ms,
                        error_code,
                        {"job_id": job_id, "cv_document_id": doc_id, "outcome": "retried"},
                    )
                    db.commit()
                    retry.append(doc_id)
                    continue
                handle(doc_id, final, thread_id, started, duration_ms, error_code)
        except BaseException:
            # e.g. the job timeout fired: don't wait for threads that may be stuck
            pool.shutdown(wait=False, cancel_futures=True)
            raise
        pool.shutdown(wait=True)
        return retry

    try:
        retry = run_batch(work, retry_failed=True)
        if retry:
            plural = "s" if len(retry) != 1 else ""
            # retrying while every model is still rate limited just fails again
            wait = min(seconds_until_available(), MAX_RETRY_WAIT)
            if wait > 0:
                report(f"Waiting {round(wait)} s for the AI service before trying again.")
                time.sleep(wait)
            report(f"Trying {len(retry)} CV{plural} again after a busy AI service.")
            run_batch([(doc_id, paths[doc_id]) for doc_id in retry], retry_failed=False)
    finally:
        # anything this batch didn't finish goes back in the queue instead of staying stuck
        db.rollback()
        release_unfinished(db, job_id, [doc_id for doc_id, _ in work])

    rank_job(db, job_id)
    seconds = round(time.monotonic() - started_batch, 1)
    log.info("screening_finished", job_id=job_id, total=total, seconds=seconds, **counts)
    return {
        "job_id": job_id,
        "total": total,
        "seconds": seconds,
        **counts,
        "failed_items": failed_items,
    }


def _save(
    db: Session, job: Job, doc_id: int, final: dict[str, Any], requirements: list[dict[str, Any]]
) -> None:
    doc = db.get(CvDocument, doc_id)
    outcome = final.get("outcome", "failed")
    doc.used_ocr = bool(final.get("used_ocr"))
    doc.language = final.get("language")
    if outcome in ("unreadable", "not_english", "failed"):
        doc.status = ExtractionStatus.FAILED if outcome == "failed" else ExtractionStatus.UNREADABLE
        doc.failure_reason = (final.get("reason") or "")[:500] or None
        if doc.candidate_id:
            # a form applicant whose cv can't be screened shows up as such, not silently
            _upsert_screening(db, job.id, doc.candidate_id, doc.id, final, ScreeningStatus.FAILED)
        return

    identity = final["identity"]
    candidate = _candidate_for(db, doc, identity)
    doc.candidate_id = candidate.id
    doc.status = ExtractionStatus.EXTRACTED
    doc.failure_reason = None

    extraction = db.scalar(select(CvExtraction).where(CvExtraction.cv_document_id == doc.id))
    if extraction is None:
        extraction = CvExtraction(cv_document_id=doc.id)
        db.add(extraction)
    # only the redacted text is kept; evidence quotes are checked against it
    extraction.raw_text = final["redacted_text"]
    extraction.structured = final["profile"]
    extraction.total_years_experience = final["profile"].get("total_years_experience")
    extraction.page_count = final.get("page_count")

    if (
        db.scalar(
            select(Application).where(
                Application.job_id == job.id, Application.candidate_id == candidate.id
            )
        )
        is None
    ):
        db.add(
            Application(
                job_id=job.id,
                candidate_id=candidate.id,
                source="direct_upload",
                submitted_at=doc.created_at,
                consent_given=False,
                cv_document_id=doc.id,
            )
        )

    status = ScreeningStatus.EXCLUDED if outcome == "excluded" else ScreeningStatus.SCREENED
    screening = _upsert_screening(db, job.id, candidate.id, doc.id, final, status)
    screening.requirement_results = [
        ScreeningRequirementResult(
            job_requirement_id=m["requirement_id"],
            status=RequirementMatchStatus(m["status"]),
            evidence_quote=m.get("evidence_quote") or None,
            evidence_verified=bool(m.get("evidence_verified")),
            reasoning=m.get("reasoning"),
            confidence=m.get("confidence"),
        )
        for m in final["matches"]
    ]


def _upsert_screening(
    db: Session,
    job_id: int,
    candidate_id: int,
    doc_id: int,
    final: dict[str, Any],
    status: ScreeningStatus,
) -> Screening:
    screening = db.scalar(
        select(Screening).where(Screening.job_id == job_id, Screening.candidate_id == candidate_id)
    )
    if screening is None:
        screening = Screening(job_id=job_id, candidate_id=candidate_id)
        db.add(screening)
    screening.cv_document_id = doc_id
    screening.status = status
    screening.fulfilment_percent = final.get("fulfilment") or 0.0
    screening.summary = final.get("summary") or None
    screening.exclusion_reason = (
        (final.get("reason") or "")[:500] or None if status != ScreeningStatus.SCREENED else None
    )
    screening.rank = None
    screening.screened_at = datetime.now(UTC)
    screening.model_name = final.get("model_name")
    screening.prompt_version = PROMPT_VERSION
    if status == ScreeningStatus.FAILED:
        screening.requirement_results = []
    db.flush()
    return screening


def _candidate_for(db: Session, doc: CvDocument, identity: dict[str, str]) -> Candidate:
    if doc.candidate_id:
        return db.get(Candidate, doc.candidate_id)
    email = (identity.get("email") or "").strip().lower() or None
    candidate = None
    if email:
        candidate = db.scalar(select(Candidate).where(func.lower(Candidate.email) == email))
    if candidate is None:
        candidate = Candidate(
            full_name=(identity.get("full_name") or "").strip()[:200] or "Name not found in CV",
            email=email,
            phone=(identity.get("phone") or "")[:50] or None,
            location=(identity.get("location") or "")[:160] or None,
        )
        db.add(candidate)
        db.flush()
    return candidate


def rank_job(db: Session, job_id: int) -> None:
    # order: share of weighted requirements met, then must-haves fully met, then the
    # model's average confidence. excluded and failed cvs get no rank
    screenings = db.scalars(
        select(Screening)
        .where(Screening.job_id == job_id)
        .options(selectinload(Screening.requirement_results))
    ).all()
    job = get_job(db, job_id)
    mandatory = {r.id for r in job.requirements if r.is_mandatory}
    names = {
        c.id: c.full_name
        for c in db.scalars(
            select(Candidate).where(Candidate.id.in_([s.candidate_id for s in screenings]))
        )
    }

    def key(s: Screening) -> tuple:
        results = s.requirement_results
        must_met = sum(
            1
            for r in results
            if r.job_requirement_id in mandatory and r.status == RequirementMatchStatus.MET
        )
        confidence = sum(r.confidence or 0 for r in results) / len(results) if results else 0
        return (-s.fulfilment_percent, -must_met, -confidence, names.get(s.candidate_id, ""))

    ranked = sorted((s for s in screenings if s.status == ScreeningStatus.SCREENED), key=key)
    for s in screenings:
        s.rank = None
    for position, s in enumerate(ranked, start=1):
        s.rank = position
    db.commit()


# --- reading results ------------------------------------------------------------------


def results(db: Session, job_id: int) -> dict[str, Any]:
    job = get_job(db, job_id)
    screenings = db.scalars(
        select(Screening)
        .where(Screening.job_id == job_id)
        .options(selectinload(Screening.requirement_results))
    ).all()
    candidates = {
        c.id: c
        for c in db.scalars(
            select(Candidate).where(Candidate.id.in_([s.candidate_id for s in screenings]))
        )
    }
    mandatory = {r.id for r in job.requirements if r.is_mandatory}

    def row(s: Screening) -> dict[str, Any]:
        results_ = s.requirement_results
        extraction = (
            db.scalar(select(CvExtraction).where(CvExtraction.cv_document_id == s.cv_document_id))
            if s.cv_document_id
            else None
        )
        profile = (extraction.structured or {}) if extraction else {}
        return {
            "candidate_id": s.candidate_id,
            "full_name": candidates[s.candidate_id].full_name,
            "status": s.status.value,
            "rank": s.rank,
            "fulfilment_percent": s.fulfilment_percent,
            "met": sum(1 for r in results_ if r.status == RequirementMatchStatus.MET),
            "partially_met": sum(
                1 for r in results_ if r.status == RequirementMatchStatus.PARTIALLY_MET
            ),
            "unmet": sum(1 for r in results_ if r.status == RequirementMatchStatus.UNMET),
            "must_have_met": sum(
                1
                for r in results_
                if r.job_requirement_id in mandatory and r.status == RequirementMatchStatus.MET
            ),
            "must_have_total": len(mandatory),
            "summary": s.summary,
            "exclusion_reason": s.exclusion_reason,
            "years_experience": profile.get("total_years_experience"),
            "headline": profile.get("headline"),
            "screened_at": s.screened_at,
        }

    rows = [row(s) for s in screenings]
    unread = [
        {
            "cv_document_id": d.id,
            "filename": d.original_filename,
            "reason": d.failure_reason,
            "candidate_name": candidates[d.candidate_id].full_name
            if d.candidate_id in candidates
            else None,
        }
        for d in db.scalars(
            select(CvDocument).where(
                CvDocument.job_id == job_id,
                CvDocument.status.in_([ExtractionStatus.UNREADABLE, ExtractionStatus.FAILED]),
            )
        )
    ]
    return {
        "ranked": sorted(
            [r for r in rows if r["status"] == "screened"], key=lambda r: r["rank"] or 10**6
        ),
        "excluded": sorted(
            [r for r in rows if r["status"] == "excluded"],
            key=lambda r: -r["fulfilment_percent"],
        ),
        "not_screened": unread,
        "pending_count": pending_count(db, job_id),
        "last_screened_at": max((s.screened_at for s in screenings if s.screened_at), default=None),
    }


def candidate_detail(db: Session, job_id: int, candidate_id: int) -> dict[str, Any]:
    job = get_job(db, job_id)
    screening = db.scalar(
        select(Screening)
        .where(Screening.job_id == job_id, Screening.candidate_id == candidate_id)
        .options(selectinload(Screening.requirement_results))
    )
    if screening is None:
        raise NotFoundError("This candidate has not been screened for this job.")
    candidate = db.get(Candidate, candidate_id)
    doc = db.get(CvDocument, screening.cv_document_id) if screening.cv_document_id else None
    extraction = (
        db.scalar(select(CvExtraction).where(CvExtraction.cv_document_id == doc.id))
        if doc
        else None
    )
    by_req = {r.job_requirement_id: r for r in screening.requirement_results}
    return {
        "candidate": {
            "id": candidate.id,
            "full_name": candidate.full_name,
            "email": candidate.email,
            "phone": candidate.phone,
            "location": candidate.location,
        },
        "status": screening.status.value,
        "rank": screening.rank,
        "fulfilment_percent": screening.fulfilment_percent,
        "summary": screening.summary,
        "exclusion_reason": screening.exclusion_reason,
        "screened_at": screening.screened_at,
        "model_name": screening.model_name,
        "cv": {
            "document_id": doc.id,
            "filename": doc.original_filename,
            "used_ocr": doc.used_ocr,
            "page_count": extraction.page_count if extraction else None,
        }
        if doc
        else None,
        "profile": (extraction.structured or {}) if extraction else {},
        "requirements": [
            {
                "requirement_id": req.id,
                "text": req.text,
                "category": req.category.value,
                "is_mandatory": req.is_mandatory,
                "weight": req.weight,
                "status": by_req[req.id].status.value if req.id in by_req else "unmet",
                "evidence_quote": by_req[req.id].evidence_quote if req.id in by_req else None,
                "evidence_verified": by_req[req.id].evidence_verified
                if req.id in by_req
                else False,
                "reasoning": by_req[req.id].reasoning if req.id in by_req else None,
                "confidence": by_req[req.id].confidence if req.id in by_req else None,
            }
            for req in job.requirements
        ],
    }
