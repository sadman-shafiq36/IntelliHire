from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.deps import require_interviewer
from app.models.user import User
from app.services import interview_reviews as service

router = APIRouter(tags=["interview reviews"])

ReviewKind = Literal["claims", "interview_score", "quality"]


class BackgroundJobStarted(BaseModel):
    background_job_id: str


class ReviewOut(BaseModel):
    status: str
    message: str | None
    result: dict[str, Any] | None
    warnings: list[str]
    model_name: str | None
    generated_at: datetime | None
    stale: bool


class Reviews(BaseModel):
    interview: dict[str, Any]
    analysis_status: str | None
    cv_score: float | None
    requirements: list[dict[str, Any]]
    turns: list[dict[str, Any]]
    claims: ReviewOut | None
    interview_score: ReviewOut | None
    quality: ReviewOut | None


@router.get(
    "/interviews/{interview_id}/reviews",
    response_model=Reviews,
    summary="CV claim checks, the interview score and the interview quality review (no AI call)",
)
def get_reviews(
    interview_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(require_interviewer),
) -> Reviews:
    return Reviews(**service.results(db, interview_id))


@router.post(
    "/interviews/{interview_id}/reviews/{kind}",
    response_model=BackgroundJobStarted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Write one of the reviews again (background job)",
)
def rerun_review(
    interview_id: int,
    kind: ReviewKind,
    db: Session = Depends(get_db),
    _user: User = Depends(require_interviewer),
) -> BackgroundJobStarted:
    return BackgroundJobStarted(background_job_id=service.start(db, interview_id, kind))
