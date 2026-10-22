from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.deps import require_interviewer
from app.models.user import User
from app.services import assessment as service

router = APIRouter(tags=["candidate assessment"])


class BackgroundJobStarted(BaseModel):
    background_job_id: str


class Component(BaseModel):
    name: str
    label: str
    score: float | None
    weight: float
    effective_weight: float
    contribution: float


class Finding(BaseModel):
    kind: str
    text: str
    evidence: list[dict[str, Any]]


class AssessmentOut(BaseModel):
    status: str
    message: str | None
    overall_score: float | None
    position: str | None
    position_label: str
    components: list[Component]
    requirements: list[dict[str, Any]]
    capped_by: list[str]
    summary: str | None
    findings: list[Finding]
    warnings: list[str]
    model_name: str
    generated_at: datetime | None
    stale: bool


class Report(BaseModel):
    interview: dict[str, Any]
    analysis_status: str | None
    assessment: AssessmentOut | None


class Comparison(BaseModel):
    job_id: int
    job_title: str
    weights: dict[str, int]
    requirements: list[dict[str, Any]]
    candidates: list[dict[str, Any]]


@router.get(
    "/interviews/{interview_id}/assessment",
    response_model=Report,
    summary="The candidate's consolidated assessment for this interview (no AI call)",
)
def get_report(
    interview_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(require_interviewer),
) -> Report:
    return Report(**service.report(db, interview_id))


@router.post(
    "/interviews/{interview_id}/assessment",
    response_model=BackgroundJobStarted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Write the consolidated assessment again (background job)",
)
def regenerate(
    interview_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(require_interviewer),
) -> BackgroundJobStarted:
    return BackgroundJobStarted(background_job_id=service.start(db, interview_id))


@router.get(
    "/interviews/{interview_id}/assessment/report.html",
    summary="The report as a printable HTML page",
    response_class=Response,
)
def report_html(
    interview_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(require_interviewer),
) -> Response:
    page = service.render_html(service.report(db, interview_id))
    return Response(page, media_type="text/html; charset=utf-8")


@router.get(
    "/interviews/{interview_id}/assessment/report.pdf",
    summary="The report as a PDF",
    response_class=Response,
)
def report_pdf(
    interview_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(require_interviewer),
) -> Response:
    pdf = service.render_pdf(service.report(db, interview_id))
    return Response(
        pdf,
        media_type="application/pdf",
        # an id, not the candidate's name, so the file name carries no personal data
        headers={"Content-Disposition": f'attachment; filename="assessment-{interview_id}.pdf"'},
    )


@router.get(
    "/jobs/{job_id}/assessments",
    response_model=Comparison,
    summary="Every assessed candidate for the job, for side-by-side comparison",
)
def compare(
    job_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(require_interviewer),
) -> Comparison:
    return Comparison(**service.compare(db, job_id))
