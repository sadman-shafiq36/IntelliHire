from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.deps import require_interviewer
from app.models.user import User
from app.services.overview import overview

router = APIRouter(prefix="/overview", tags=["overview"])


class Pipeline(BaseModel):
    roles: int
    described: int
    live: int
    applications: int
    new_this_week: int
    screened: int
    shortlist_ready: int
    scored: int
    shortlisted: int
    selected: int
    interviews_upcoming: int
    interviewed: int = 0
    interviews_live: int = 0


class AttentionItem(BaseModel):
    kind: str
    title: str
    detail: str
    job_id: int | None


class RecentApplication(BaseModel):
    id: int
    job_id: int
    job_title: str
    full_name: str
    submitted_at: datetime


class LiveJob(BaseModel):
    id: int
    title: str
    location: str | None
    applications: int
    post_url: str | None


class Overview(BaseModel):
    pipeline: Pipeline
    policies_indexed: int
    attention: list[AttentionItem]
    recent_applications: list[RecentApplication]
    live_jobs: list[LiveJob]


@router.get("", response_model=Overview, summary="Counts and to-dos for the overview page")
def get_overview(
    db: Session = Depends(get_db),
    _user: User = Depends(require_interviewer),
) -> Overview:
    return Overview(**overview(db))
