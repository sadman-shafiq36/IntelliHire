# scoring a job's screened candidates: the job's weights and key terms are set once
# (the ai proposes, a person may change the weights), each candidate runs through the
# scoring graph on a worker thread, results are saved one by one on this thread, then
# ranked. changing weights only recomputes aggregates, no ai call

import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.agents.checkpointer import thread_config
from app.agents.runner import new_thread_id, record_run
from app.agents.scoring import checks
from app.agents.scoring.llm import PROMPT_VERSION, WEIGHTS_PROMPT_VERSION, Proposer
from app.config import settings
from app.errors import AppError, ExternalServiceError, InvalidInputError, NotFoundError
from app.logging_config import get_logger
from app.models.candidate import Candidate, CvExtraction
from app.models.enums import ScoreDimensionName, ScreeningStatus
from app.models.job import Job
from app.models.screening import CandidateScore, ScoreDimension, Screening
from app.services import ai
from app.services.ai import seconds_until_available
from app.services.jobs import get_job

log = get_logger(__name__)

ProgressFn = Callable[..., None]
Embedder = Callable[[list[str]], list[list[float]]]
MAX_RETRY_WAIT = 90.0
MAX_KEYWORDS = 15
# a key term is a skill or tool; a longer phrase ("BSc in Computer Science") is never
# written the same way in a cv and only penalises people
MAX_KEYWORD_WORDS = 3


# --- the job's weights and key terms ---------------------------------------------------


def job_requirements(job: Job) -> list[dict[str, Any]]:
    return [
        {
            "id": r.id,
            "text": r.text,
            "category": r.category.value,
            "dimension": checks.dimension_for(r.category.value),
            "is_mandatory": r.is_mandatory,
            "weight": r.weight,
        }
        for r in job.requirements
    ]


def applicable_dimensions(job: Job) -> list[str]:
    used = {checks.dimension_for(r.category.value) for r in job.requirements}
    if job.min_years_experience:
        used.add(checks.EXPERIENCE)
    return [d for d in checks.DIMENSIONS if d in used or d == checks.JD_MATCH]


def description_text(job: Job) -> str:
    if job.description_md:
        return job.description_md
    # no description yet: the profile is the closest thing to one
    lines = [job.title, job.summary or "", *[r.text for r in job.requirements]]
    return "\n".join(line for line in lines if line)


def _job_text(job: Job) -> str:
    return "\n".join(
        [
            job.title,
            job.summary or "",
            job.description_md or "",
            *[r.text for r in job.requirements],
        ]
    )


def suggest_profile(job: Job, proposer: Proposer) -> tuple[dict[str, Any] | None, list[str]]:
    # the ai's weights, reasons and key terms after checking them, or None if the ai
    # could not be reached
    applicable = applicable_dimensions(job)
    payload = {
        "title": job.title,
        "min_years_experience": job.min_years_experience,
        "requirements": job_requirements(job),
        "description": description_text(job),
    }
    try:
        result, model = proposer(payload)
    except AppError as exc:
        log.warning("score_weights_failed", job_id=job.id, error_type=type(exc).__name__)
        return None, [exc.message]
    raw = {w["dimension"]: w.get("weight") or 0 for w in result.get("weights", [])}
    reasons = {w["dimension"]: (w.get("reason") or "").strip() for w in result.get("weights", [])}
    weights = checks.normalise_weights(raw, applicable)
    return {
        "weights": weights,
        "reasons": {d: reasons.get(d) or "" for d in checks.DIMENSIONS if weights[d]},
        "keywords": check_keywords(job, result.get("keywords", [])),
        "model_name": model,
        "prompt_version": WEIGHTS_PROMPT_VERSION,
    }, []


def check_keywords(job: Job, keywords: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # a key term counts only if the job's own text uses it, so none is invented
    text = f" {checks.normalise(_job_text(job))} "
    seen: set[str] = set()
    kept = []
    for kw in keywords:
        term = (kw.get("term") or "").strip()[:60]
        key = checks.normalise(term)
        if (
            not key
            or key in seen
            or len(key.split()) > MAX_KEYWORD_WORDS
            or not checks.contains_term(term, text)
        ):
            continue
        seen.add(key)
        aliases = [a.strip()[:60] for a in kw.get("aliases") or [] if a and a.strip()][:4]
        kept.append({"term": term, "aliases": aliases})
    return kept[:MAX_KEYWORDS]


def _apply_suggestion(profile: dict[str, Any], suggestion: dict[str, Any]) -> None:
    keywords = suggestion.pop("keywords")
    profile["suggested"] = suggestion
    # key terms are fixed once set, so every candidate's description match uses the same
    if not profile.get("keywords"):
        profile["keywords"] = keywords


def ensure_profile(db: Session, job: Job, proposer: Proposer) -> tuple[dict[str, Any], list[str]]:
    profile = dict(job.score_profile or {})
    warnings: list[str] = []
    if profile.get("suggested") is None:
        suggestion, warnings = suggest_profile(job, proposer)
        if suggestion is not None:
            _apply_suggestion(profile, suggestion)
            # weights a person already set are kept
            if profile.get("source") != "edited":
                profile["weights"] = suggestion["weights"]
                profile["source"] = "ai"
    if not profile.get("weights"):
        profile["weights"] = checks.normalise_weights(
            checks.DEFAULT_WEIGHTS, applicable_dimensions(job)
        )
        profile["source"] = "default"
        warnings.append("The AI could not suggest weights, so standard weights are used.")
    profile.setdefault("keywords", [])
    job.score_profile = profile
    db.commit()
    return profile, warnings


def resuggest_weights(db: Session, job_id: int, proposer: Proposer) -> dict[str, Any]:
    job = get_job(db, job_id)
    profile = dict(job.score_profile or {})
    suggestion, warnings = suggest_profile(job, proposer)
    if suggestion is None:
        raise ExternalServiceError(warnings[0] if warnings else "The AI could not suggest weights.")
    _apply_suggestion(profile, suggestion)
    profile.update(weights=suggestion["weights"], source="ai")
    job.score_profile = profile
    db.commit()
    recompute(db, job_id)
    return {"job_id": job_id, "weights": profile["weights"]}


def set_weights(db: Session, job_id: int, weights: dict[str, float]) -> dict[str, Any]:
    job = get_job(db, job_id)
    unknown = set(weights) - set(checks.DIMENSIONS)
    if unknown:
        raise InvalidInputError("Unknown scoring dimension.", {"dimensions": sorted(unknown)})
    if any(not 0 <= float(v) <= 100 for v in weights.values()):
        raise InvalidInputError("Each weight must be between 0 and 100.")
    applicable = applicable_dimensions(job)
    if not any(float(weights.get(d) or 0) > 0 for d in applicable):
        raise InvalidInputError("Give at least one dimension a weight above 0.")
    profile = dict(job.score_profile or {})
    profile.update(weights=checks.normalise_weights(weights, applicable), source="edited")
    profile.setdefault("keywords", [])
    profile.setdefault("suggested", None)
    job.score_profile = profile
    db.commit()
    recompute(db, job_id)
    log.info("score_weights_edited", job_id=job_id)
    return profile


def recompute(db: Session, job_id: int) -> None:
    job = get_job(db, job_id)
    weights = (job.score_profile or {}).get("weights") or {}
    for score in _scores(db, job_id):
        for dim in score.dimensions:
            dim.weight = float(weights.get(dim.dimension.value, 0))
        score.aggregate_score = checks.aggregate(
            {d.dimension.value: d.raw_score for d in score.dimensions}, weights
        )
    db.commit()
    rank_scores(db, job_id)


# --- running a batch -----------------------------------------------------------------


def _scores(db: Session, job_id: int) -> list[CandidateScore]:
    return list(
        db.scalars(
            select(CandidateScore)
            .where(CandidateScore.job_id == job_id)
            .options(selectinload(CandidateScore.dimensions))
        )
    )


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def is_stale(score: CandidateScore, screening: Screening) -> bool:
    return _aware(score.based_on_screened_at) != _aware(screening.screened_at)


def _to_score(db: Session, job_id: int, rescore: bool) -> list[Screening]:
    screenings = db.scalars(
        select(Screening).where(
            Screening.job_id == job_id, Screening.status == ScreeningStatus.SCREENED
        )
    ).all()
    scores = {s.candidate_id: s for s in _scores(db, job_id)}
    return [
        s
        for s in screenings
        if rescore or s.candidate_id not in scores or is_stale(scores[s.candidate_id], s)
    ]


def start_scoring(db: Session, job_id: int, rescore: bool) -> int:
    get_job(db, job_id)
    count = len(_to_score(db, job_id, rescore))
    if count == 0:
        if not _to_score(db, job_id, True):
            raise InvalidInputError("Screen CVs first. Only candidates in the ranking are scored.")
        raise InvalidInputError("Everyone in the ranking already has an up-to-date score.")
    return count


def score_job(
    db: Session,
    job_id: int,
    graph: Any,
    proposer: Proposer,
    rescore: bool = False,
    progress: ProgressFn | None = None,
    concurrency: int | None = None,
    embedder: Embedder = ai.embed_texts,
) -> dict[str, Any]:
    job = get_job(db, job_id)
    total = 0
    done = 0
    failed_items: list[dict[str, Any]] = []

    def report(message: str) -> None:
        if progress:
            progress(
                state="running",
                message=message,
                total=total,
                completed=done,
                failed_items=failed_items,
            )

    report("Setting the weights for this job.")
    profile, profile_warnings = ensure_profile(db, job, proposer)
    requirements = job_requirements(job)
    job_info = {"title": job.title, "min_years_experience": job.min_years_experience}
    [description_vector] = embedder([description_text(job)[: checks.EMBED_MAX_CHARS]])

    work: dict[int, dict[str, Any]] = {}
    for screening in _to_score(db, job_id, rescore):
        db.refresh(screening, ["requirement_results"])
        extraction = db.scalar(
            select(CvExtraction).where(CvExtraction.cv_document_id == screening.cv_document_id)
        )
        if extraction is None or not extraction.raw_text:
            continue
        work[screening.candidate_id] = {
            "candidate_id": screening.candidate_id,
            "job": job_info,
            "requirements": requirements,
            "results": [
                {
                    "requirement_id": r.job_requirement_id,
                    "status": r.status.value,
                    "evidence_quote": r.evidence_quote or "",
                }
                for r in screening.requirement_results
            ],
            "cv_text": extraction.raw_text,
            "years": extraction.total_years_experience,
            "keywords": profile["keywords"],
            "description_vector": description_vector,
            "screened_at": screening.screened_at,
        }
    total = len(work)
    counts = {"scored": 0, "failed": 0}
    started_batch = time.monotonic()

    def run_one(candidate_id: int) -> tuple[int, dict[str, Any], str, datetime, int, str | None]:
        thread_id = new_thread_id("score")
        started = datetime.now(UTC)
        t0 = time.monotonic()
        error_code = None
        inputs = {k: v for k, v in work[candidate_id].items() if k != "screened_at"}
        try:
            final = graph.invoke(
                {"thread_id": thread_id, **inputs, "errors": []},
                config=thread_config(thread_id),
            )
        except Exception as exc:
            log.exception(
                "scoring_crashed", candidate_id=candidate_id, error_type=type(exc).__name__
            )
            final = {
                "outcome": "failed",
                "reason": "Something went wrong while scoring. Try scoring again.",
            }
            error_code = type(exc).__name__
        if final.get("outcome") == "failed" and not error_code:
            error_code = "llm_failed"
        return (
            candidate_id,
            final,
            thread_id,
            started,
            int((time.monotonic() - t0) * 1000),
            error_code,
        )

    def handle(candidate_id, final, thread_id, started, duration_ms, error_code) -> None:
        nonlocal done
        outcome = final.get("outcome", "failed")
        if outcome == "scored":
            _save(db, job, candidate_id, final, profile, work[candidate_id]["screened_at"])
        else:
            failed_items.append(
                {"item_ref": str(candidate_id), "reason": final.get("reason") or "Not scored."}
            )
        record_run(
            db,
            "candidate_scoring",
            thread_id,
            started,
            duration_ms,
            error_code,
            {"job_id": job_id, "candidate_id": candidate_id, "outcome": outcome},
        )
        db.commit()
        counts[outcome] = counts.get(outcome, 0) + 1
        done += 1
        report(f"Scored {done} of {total}.")

    def run_batch(ids: list[int], retry_failed: bool) -> list[int]:
        retry: list[int] = []
        pool = ThreadPoolExecutor(max_workers=max(1, concurrency or settings.screening_concurrency))
        try:
            futures = [pool.submit(run_one, cid) for cid in ids]
            for future in as_completed(futures):
                result = future.result()
                # an ai hiccup gets one more go at the end
                if retry_failed and result[1].get("outcome") == "failed":
                    candidate_id, _, thread_id, started, duration_ms, error_code = result
                    record_run(
                        db,
                        "candidate_scoring",
                        thread_id,
                        started,
                        duration_ms,
                        error_code,
                        {"job_id": job_id, "candidate_id": candidate_id, "outcome": "retried"},
                    )
                    db.commit()
                    retry.append(candidate_id)
                    continue
                handle(*result)
        except BaseException:
            pool.shutdown(wait=False, cancel_futures=True)
            raise
        pool.shutdown(wait=True)
        return retry

    report(f"Scoring {total} candidate{'s' if total != 1 else ''}.")
    retry = run_batch(list(work), retry_failed=True)
    if retry:
        wait = min(seconds_until_available(), MAX_RETRY_WAIT)
        if wait > 0:
            report(f"Waiting {round(wait)} s for the AI service before trying again.")
            time.sleep(wait)
        report(f"Trying {len(retry)} again after a busy AI service.")
        run_batch(retry, retry_failed=False)

    rank_scores(db, job_id)
    seconds = round(time.monotonic() - started_batch, 1)
    log.info("scoring_finished", job_id=job_id, total=total, seconds=seconds, **counts)
    return {
        "job_id": job_id,
        "total": total,
        "seconds": seconds,
        **counts,
        "failed_items": failed_items,
        "warnings": profile_warnings,
    }


def _save(
    db: Session,
    job: Job,
    candidate_id: int,
    final: dict[str, Any],
    profile: dict[str, Any],
    screened_at: datetime | None,
) -> None:
    score = db.scalar(
        select(CandidateScore).where(
            CandidateScore.job_id == job.id, CandidateScore.candidate_id == candidate_id
        )
    )
    if score is None:
        score = CandidateScore(job_id=job.id, candidate_id=candidate_id)
        db.add(score)
    weights = profile["weights"]
    dims = final["dimensions"]
    score.dimensions = [
        ScoreDimension(
            dimension=ScoreDimensionName(d),
            raw_score=float(dims[d]["score"]),
            base_score=float(dims[d]["base_score"]),
            adjustment=float(dims[d]["adjustment"]),
            weight=float(weights.get(d, 0)),
            justification=dims[d]["justification"],
            details=dims[d]["details"],
        )
        for d in checks.DIMENSIONS
        if d in dims
    ]
    score.aggregate_score = checks.aggregate({d: v["score"] for d, v in dims.items()}, weights)
    # no model answered when there was nothing for it to judge
    score.model_name = final.get("model_name") or "none"
    score.prompt_version = PROMPT_VERSION
    score.computed_at = datetime.now(UTC)
    score.based_on_screened_at = screened_at
    score.warnings = final.get("warnings") or None
    score.rank = None
    db.flush()


def rank_scores(db: Session, job_id: int) -> None:
    # aggregate first, then technical skills, then screening coverage, then name.
    # only candidates still in the screening ranking are ranked
    scores = _scores(db, job_id)
    screenings = {
        s.candidate_id: s for s in db.scalars(select(Screening).where(Screening.job_id == job_id))
    }
    names = _names(db, [s.candidate_id for s in scores])

    def key(s: CandidateScore) -> tuple:
        tech = next(
            (d.raw_score for d in s.dimensions if d.dimension.value == "technical_skills"), 0
        )
        screening = screenings.get(s.candidate_id)
        return (
            -s.aggregate_score,
            -tech,
            -(screening.fulfilment_percent if screening else 0),
            names.get(s.candidate_id, ""),
        )

    ranked = sorted(
        (
            s
            for s in scores
            if (sc := screenings.get(s.candidate_id)) and sc.status == ScreeningStatus.SCREENED
        ),
        key=key,
    )
    for s in scores:
        s.rank = None
    for position, s in enumerate(ranked, start=1):
        s.rank = position
    db.commit()


def _names(db: Session, ids: list[int]) -> dict[int, str]:
    return {c.id: c.full_name for c in db.scalars(select(Candidate).where(Candidate.id.in_(ids)))}


# --- people's decisions ----------------------------------------------------------------


def _score_for(db: Session, job_id: int, candidate_id: int) -> CandidateScore:
    score = db.scalar(
        select(CandidateScore)
        .where(CandidateScore.job_id == job_id, CandidateScore.candidate_id == candidate_id)
        .options(selectinload(CandidateScore.dimensions))
    )
    if score is None:
        raise NotFoundError("This candidate has not been scored for this job.")
    return score


def update_decision(
    db: Session,
    job_id: int,
    candidate_id: int,
    shortlisted: bool | None,
    selected: bool | None,
) -> CandidateScore:
    score = _score_for(db, job_id, candidate_id)
    if score.rank is None and (shortlisted or selected):
        raise InvalidInputError(
            "Only candidates in the score ranking can be shortlisted. Score them again first."
        )
    if shortlisted is not None:
        score.is_shortlisted = shortlisted
        if not shortlisted:
            score.selected_at = None
    if selected is not None:
        score.selected_at = datetime.now(UTC) if selected else None
        if selected:
            # selecting someone for interview puts them on the shortlist too
            score.is_shortlisted = True
    db.commit()
    log.info(
        "score_decision",
        job_id=job_id,
        candidate_id=candidate_id,
        shortlisted=score.is_shortlisted,
        selected=score.selected_at is not None,
    )
    return score


# --- reading results -----------------------------------------------------------------


def _profile_out(job: Job) -> dict[str, Any] | None:
    profile = job.score_profile
    if not profile:
        return None
    return {
        "weights": profile.get("weights") or {},
        "source": profile.get("source") or "default",
        "suggested": profile.get("suggested"),
        "keywords": profile.get("keywords") or [],
        "applicable": applicable_dimensions(job),
    }


def results(db: Session, job_id: int) -> dict[str, Any]:
    job = get_job(db, job_id)
    scores = _scores(db, job_id)
    screenings = {
        s.candidate_id: s for s in db.scalars(select(Screening).where(Screening.job_id == job_id))
    }
    names = _names(db, [s.candidate_id for s in scores])
    rows = []
    for s in scores:
        screening = screenings.get(s.candidate_id)
        if s.rank is None or screening is None:
            continue
        extraction = (
            db.scalar(
                select(CvExtraction).where(CvExtraction.cv_document_id == screening.cv_document_id)
            )
            if screening.cv_document_id
            else None
        )
        profile = (extraction.structured or {}) if extraction else {}
        rows.append(
            {
                "candidate_id": s.candidate_id,
                "full_name": names.get(s.candidate_id, ""),
                "rank": s.rank,
                "aggregate_score": s.aggregate_score,
                "dimensions": {d.dimension.value: d.raw_score for d in s.dimensions},
                "is_shortlisted": s.is_shortlisted,
                "selected_at": s.selected_at,
                "stale": is_stale(s, screening),
                "fulfilment_percent": screening.fulfilment_percent,
                "headline": profile.get("headline"),
                "years_experience": profile.get("total_years_experience"),
                "computed_at": s.computed_at,
                "warning_count": len(s.warnings or []),
            }
        )
    rows.sort(key=lambda r: r["rank"])
    excluded = sum(1 for s in screenings.values() if s.status == ScreeningStatus.EXCLUDED)
    return {
        "profile": _profile_out(job),
        "ranked": rows,
        "waiting_count": len(_to_score(db, job_id, False)),
        "excluded_count": excluded,
        "last_scored_at": max((s.computed_at for s in scores if s.computed_at), default=None),
    }


def breakdown(db: Session, job_id: int, candidate_id: int) -> dict[str, Any]:
    job = get_job(db, job_id)
    score = _score_for(db, job_id, candidate_id)
    candidate = db.get(Candidate, candidate_id)
    screening = db.scalar(
        select(Screening).where(Screening.job_id == job_id, Screening.candidate_id == candidate_id)
    )
    used = {d.dimension.value: d.weight for d in score.dimensions if d.weight > 0}
    total_weight = sum(used.values()) or 1.0
    weights = (job.score_profile or {}).get("weights") or {}
    return {
        "candidate": {"id": candidate.id, "full_name": candidate.full_name},
        "rank": score.rank,
        "aggregate_score": score.aggregate_score,
        "is_shortlisted": score.is_shortlisted,
        "selected_at": score.selected_at,
        "stale": is_stale(score, screening) if screening else True,
        "model_name": score.model_name,
        "prompt_version": score.prompt_version,
        "computed_at": score.computed_at,
        "warnings": score.warnings or [],
        "dimensions": [
            {
                "dimension": d.dimension.value,
                "score": d.raw_score,
                "base_score": d.base_score,
                "adjustment": d.adjustment,
                "weight": d.weight,
                # points this dimension adds to the aggregate
                "contribution": round(d.raw_score * d.weight / total_weight, 1)
                if d.weight > 0
                else 0.0,
                "justification": d.justification,
                "details": d.details or {},
            }
            for d in score.dimensions
        ],
        # weighted dimensions this candidate has no score for (no requirements in them)
        "not_scored": [
            d
            for d in checks.DIMENSIONS
            if weights.get(d) and d not in {x.dimension.value for x in score.dimensions}
        ],
    }


def counts_by_job(db: Session) -> dict[int, dict[str, int]]:
    # for the jobs list and the overview: scored, waiting to be scored, shortlisted, selected
    screenings = db.scalars(
        select(Screening).where(Screening.status == ScreeningStatus.SCREENED)
    ).all()
    scores = {(s.job_id, s.candidate_id): s for s in db.scalars(select(CandidateScore))}
    out: dict[int, dict[str, int]] = {}
    for s in screenings:
        row = out.setdefault(s.job_id, {"scored": 0, "waiting": 0, "shortlisted": 0, "selected": 0})
        score = scores.get((s.job_id, s.candidate_id))
        if score is None or is_stale(score, s):
            row["waiting"] += 1
        if score is not None:
            row["scored"] += 1
            row["shortlisted"] += int(score.is_shortlisted)
            row["selected"] += int(score.selected_at is not None)
    return out
