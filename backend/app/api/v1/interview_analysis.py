from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.deps import require_interviewer
from app.models.user import User
from app.services import interview_analysis as service

router = APIRouter(tags=["interview analysis"])


class BackgroundJobStarted(BaseModel):
    background_job_id: str


class AnalysisRun(BaseModel):
    status: str
    message: str | None
    segmented_by: str | None
    analysed_at: datetime | None
    model_name: str | None
    warnings: list[str]
    stale: bool


class QaPairOut(BaseModel):
    id: int
    sequence: int
    question: str
    answer: str
    question_turn_ids: list[int]
    answer_turn_ids: list[int]
    follow_up_to: int | None
    scores: dict[str, float | None]
    score: float | None
    accuracy_basis: str | None
    justification: str | None
    sources: list[dict[str, Any]]
    requirements: list[dict[str, Any]]


class InterviewAnalysisOut(BaseModel):
    interview: dict[str, Any]
    analysis: AnalysisRun | None
    summary: dict[str, Any]
    pairs: list[QaPairOut]
    turns: list[dict[str, Any]]
    coverage: list[dict[str, Any]]
    follow_ups: list[dict[str, Any]]


@router.get(
    "/interviews/{interview_id}/analysis",
    response_model=InterviewAnalysisOut,
    summary="The interview's question-answer analysis and requirement coverage (no AI call)",
)
def get_analysis(
    interview_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(require_interviewer),
) -> InterviewAnalysisOut:
    return InterviewAnalysisOut(**service.results(db, interview_id))


@router.post(
    "/interviews/{interview_id}/analysis",
    response_model=BackgroundJobStarted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Analyse the interview again (background job)",
)
def reanalyse(
    interview_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(require_interviewer),
) -> BackgroundJobStarted:
    return BackgroundJobStarted(background_job_id=service.start(db, interview_id))
