from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.deps import require_interviewer
from app.models.user import User
from app.services import scoring as service
from app.worker import queue

router = APIRouter(tags=["candidate scoring"])


class ScoringStart(BaseModel):
    rescore: bool = False


class ScoringStarted(BaseModel):
    background_job_id: str
    candidate_count: int


class BackgroundJobStarted(BaseModel):
    background_job_id: str


class Keyword(BaseModel):
    term: str
    aliases: list[str]


class WeightSuggestion(BaseModel):
    weights: dict[str, int]
    reasons: dict[str, str]
    model_name: str | None = None
    prompt_version: str | None = None


class ScoreProfile(BaseModel):
    weights: dict[str, int]
    source: str
    suggested: WeightSuggestion | None
    keywords: list[Keyword]
    applicable: list[str]


class ScoredCandidate(BaseModel):
    candidate_id: int
    full_name: str
    rank: int
    aggregate_score: float
    dimensions: dict[str, float]
    is_shortlisted: bool
    selected_at: datetime | None
    stale: bool
    fulfilment_percent: float
    headline: str | None
    years_experience: float | None
    computed_at: datetime | None
    warning_count: int


class ScoringResults(BaseModel):
    profile: ScoreProfile | None
    ranked: list[ScoredCandidate]
    waiting_count: int
    excluded_count: int
    last_scored_at: datetime | None


class DimensionScore(BaseModel):
    dimension: str
    score: float
    base_score: float | None
    adjustment: float
    weight: float
    contribution: float
    justification: str | None
    details: dict[str, Any]


class ScoreBreakdown(BaseModel):
    candidate: dict[str, Any]
    rank: int | None
    aggregate_score: float
    is_shortlisted: bool
    selected_at: datetime | None
    stale: bool
    model_name: str
    prompt_version: str
    computed_at: datetime | None
    warnings: list[str]
    dimensions: list[DimensionScore]
    not_scored: list[str]


class WeightsIn(BaseModel):
    weights: dict[str, float] = Field(
        description="relative weight per dimension, 0-100; stored as percentages adding to 100"
    )


class DecisionIn(BaseModel):
    shortlisted: bool | None = None
    selected_for_interview: bool | None = None


class Decision(BaseModel):
    candidate_id: int
    is_shortlisted: bool
    selected_at: datetime | None


@router.post(
    "/jobs/{job_id}/scoring",
    response_model=ScoringStarted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Score the job's ranked candidates (background job)",
)
def start_scoring(
    job_id: int,
    payload: ScoringStart,
    db: Session = Depends(get_db),
    _user: User = Depends(require_interviewer),
) -> ScoringStarted:
    count = service.start_scoring(db, job_id, payload.rescore)
    background_job_id = queue.enqueue(
        "app.worker.tasks.score_job_task",
        "candidate_scoring",
        job_id=job_id,
        rescore=payload.rescore,
    )
    return ScoringStarted(background_job_id=background_job_id, candidate_count=count)


@router.get(
    "/jobs/{job_id}/scoring",
    response_model=ScoringResults,
    summary="Candidates ranked by weighted score, with the job's weights",
)
def get_results(
    job_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(require_interviewer),
) -> ScoringResults:
    return ScoringResults(**service.results(db, job_id))


@router.get(
    "/jobs/{job_id}/scoring/{candidate_id}",
    response_model=ScoreBreakdown,
    summary="One candidate's score per dimension, with the justification and evidence",
)
def get_breakdown(
    job_id: int,
    candidate_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(require_interviewer),
) -> ScoreBreakdown:
    return ScoreBreakdown(**service.breakdown(db, job_id, candidate_id))


@router.put(
    "/jobs/{job_id}/scoring/weights",
    response_model=ScoreProfile,
    summary="Set the dimension weights; aggregates and ranks are recomputed, no AI call",
)
def set_weights(
    job_id: int,
    payload: WeightsIn,
    db: Session = Depends(get_db),
    _user: User = Depends(require_interviewer),
) -> ScoreProfile:
    service.set_weights(db, job_id, payload.weights)
    return ScoreProfile(**service.results(db, job_id)["profile"])


@router.post(
    "/jobs/{job_id}/scoring/weights/suggest",
    response_model=BackgroundJobStarted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Ask the AI to propose weights again (background job)",
)
def suggest_weights(
    job_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(require_interviewer),
) -> BackgroundJobStarted:
    from app.services.jobs import get_job

    get_job(db, job_id)
    background_job_id = queue.enqueue(
        "app.worker.tasks.suggest_score_weights_task", "score_weights", job_id=job_id
    )
    return BackgroundJobStarted(background_job_id=background_job_id)


@router.patch(
    "/jobs/{job_id}/scoring/{candidate_id}",
    response_model=Decision,
    summary="Shortlist a candidate or select them for interview",
)
def decide(
    job_id: int,
    candidate_id: int,
    payload: DecisionIn,
    db: Session = Depends(get_db),
    _user: User = Depends(require_interviewer),
) -> Decision:
    score = service.update_decision(
        db, job_id, candidate_id, payload.shortlisted, payload.selected_for_interview
    )
    return Decision(
        candidate_id=candidate_id,
        is_shortlisted=score.is_shortlisted,
        selected_at=score.selected_at,
    )
