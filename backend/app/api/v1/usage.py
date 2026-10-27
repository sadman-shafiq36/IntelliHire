from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.deps import require_interviewer
from app.models.user import User
from app.services.llm_usage import summary

router = APIRouter(prefix="/usage", tags=["usage"])


class UsageGroup(BaseModel):
    name: str | None
    calls: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class RecentRun(BaseModel):
    id: int
    graph_name: str
    status: str
    started_at: datetime | None
    duration_ms: int | None
    calls: int | None
    total_tokens: int | None


class UsageSummary(BaseModel):
    days: int
    calls: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    by_model: list[UsageGroup]
    by_source: list[UsageGroup]
    recent_runs: list[RecentRun]


@router.get("", response_model=UsageSummary, summary="Tokens used by AI calls, from stored counts")
def usage(
    days: int = Query(1, ge=1, le=90),
    db: Session = Depends(get_db),
    _user: User = Depends(require_interviewer),
) -> UsageSummary:
    return UsageSummary(**summary(db, days))
