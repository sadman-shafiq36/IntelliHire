from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, File, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.deps import require_interviewer
from app.models.user import User
from app.services import screening as service
from app.worker import queue

router = APIRouter(tags=["cv screening"])


class UploadResult(BaseModel):
    accepted: list[dict[str, Any]]
    rejected: list[dict[str, str]]


class CvDocumentOut(BaseModel):
    id: int
    filename: str
    source: str
    status: str
    failure_reason: str | None
    used_ocr: bool
    candidate_name: str | None
    uploaded_at: datetime


class ScreeningStart(BaseModel):
    rescreen: bool = False


class ScreeningStarted(BaseModel):
    background_job_id: str
    cv_count: int


class RankedCandidate(BaseModel):
    candidate_id: int
    full_name: str
    status: str
    rank: int | None
    fulfilment_percent: float
    met: int
    partially_met: int
    unmet: int
    must_have_met: int
    must_have_total: int
    summary: str | None
    exclusion_reason: str | None
    years_experience: float | None
    headline: str | None
    screened_at: datetime | None


class NotScreened(BaseModel):
    cv_document_id: int
    filename: str
    reason: str | None
    candidate_name: str | None


class ScreeningResults(BaseModel):
    ranked: list[RankedCandidate]
    excluded: list[RankedCandidate]
    not_screened: list[NotScreened]
    pending_count: int
    last_screened_at: datetime | None


class RequirementEvidence(BaseModel):
    requirement_id: int
    text: str
    category: str
    is_mandatory: bool
    weight: float
    status: str
    evidence_quote: str | None
    evidence_verified: bool
    reasoning: str | None
    confidence: float | None


class CandidateScreening(BaseModel):
    candidate: dict[str, Any]
    status: str
    rank: int | None
    fulfilment_percent: float
    summary: str | None
    exclusion_reason: str | None
    screened_at: datetime | None
    model_name: str | None
    cv: dict[str, Any] | None
    profile: dict[str, Any]
    requirements: list[RequirementEvidence]


@router.post(
    "/jobs/{job_id}/cvs",
    response_model=UploadResult,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a batch of CVs (PDF or DOCX) for a job",
)
async def upload_cvs(
    job_id: int,
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_interviewer),
) -> UploadResult:
    payload = [(f.filename or "cv", f.content_type or "", await f.read()) for f in files]
    return UploadResult(**service.upload_cvs(db, job_id, payload, user.id))


@router.get("/jobs/{job_id}/cvs", response_model=list[CvDocumentOut], summary="A job's CVs")
def list_cvs(
    job_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(require_interviewer),
) -> list[CvDocumentOut]:
    return [CvDocumentOut(**d) for d in service.list_documents(db, job_id)]


@router.get("/cv-documents/{doc_id}/file", summary="Download a CV file")
def download_cv(
    doc_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(require_interviewer),
) -> FileResponse:
    doc = service.document_file(db, doc_id)
    return FileResponse(
        doc.storage_path, media_type=doc.content_type, filename=doc.original_filename
    )


@router.post(
    "/jobs/{job_id}/screening",
    response_model=ScreeningStarted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Screen the job's new CVs (background job)",
)
def start_screening(
    job_id: int,
    payload: ScreeningStart,
    db: Session = Depends(get_db),
    _user: User = Depends(require_interviewer),
) -> ScreeningStarted:
    count = service.start_screening(db, job_id, payload.rescreen)
    background_job_id = queue.enqueue(
        "app.worker.tasks.screen_job_task", "cv_screening", job_id=job_id
    )
    return ScreeningStarted(background_job_id=background_job_id, cv_count=count)


@router.get(
    "/jobs/{job_id}/screening",
    response_model=ScreeningResults,
    summary="Ranked candidates, excluded candidates and CVs that could not be screened",
)
def get_results(
    job_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(require_interviewer),
) -> ScreeningResults:
    return ScreeningResults(**service.results(db, job_id))


@router.get(
    "/jobs/{job_id}/screening/{candidate_id}",
    response_model=CandidateScreening,
    summary="One candidate's verdict per requirement, with the evidence from their CV",
)
def get_candidate(
    job_id: int,
    candidate_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(require_interviewer),
) -> CandidateScreening:
    return CandidateScreening(**service.candidate_detail(db, job_id, candidate_id))
