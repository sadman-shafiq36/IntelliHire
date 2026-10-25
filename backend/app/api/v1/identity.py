import base64
import binascii
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.deps import require_interviewer
from app.errors import InvalidInputError
from app.models.user import User
from app.services import identity as service

router = APIRouter(tags=["candidate identification"])


class LinkIdentity(BaseModel):
    consent: str | None
    reference: str | None
    source: str | None
    text: str
    version: str


class Photos(BaseModel):
    # jpeg frames from the candidate's camera, base64; kept only as a template
    images: list[str] = Field(min_length=1, max_length=service.MAX_SELFIES)


class IdentityOut(BaseModel):
    status: str
    message: str
    consent: str | None
    reference: str | None
    profile_available: bool
    method: str | None = None
    reason: str | None = None
    score: float | None = None
    margin: float | None = None
    checked_at: datetime | None = None


class Decision(BaseModel):
    decision: Literal["confirm", "reject"]


class Profile(BaseModel):
    name: str
    headline: str
    years_experience: float | None
    current_role: dict[str, str] | None
    skills: list[str]
    requirements_met: list[dict[str, Any]]
    score: float | None
    rank: int | None
    probe: list[dict[str, Any]]
    summary: str | None


def _decode(images: list[str]) -> list[bytes]:
    out = []
    for i in images:
        # a data url from a canvas, or the bare base64
        data = i.split(",", 1)[1] if i.startswith("data:") else i
        if len(data) > service.MAX_SELFIE_BYTES * 4 // 3 + 4:
            raise InvalidInputError("A picture is too large.")
        try:
            out.append(base64.b64decode(data, validate=True))
        except (binascii.Error, ValueError) as exc:
            raise InvalidInputError("A picture couldn't be read. Try again.") from exc
    return out


# --- the candidate's link (no sign-in) -------------------------------------------------


@router.get(
    "/interviews/join/{token}/identity",
    response_model=LinkIdentity,
    summary="Whether the candidate agreed to face matching, and the wording they are shown",
)
def link_identity(token: str, db: Session = Depends(get_db)) -> LinkIdentity:
    return LinkIdentity(**service.link_status(db, token))


@router.post(
    "/interviews/join/{token}/identity/photo",
    response_model=LinkIdentity,
    summary="The candidate agrees and gives a photo from their camera; only a template is kept",
)
def give_photo(token: str, payload: Photos, db: Session = Depends(get_db)) -> LinkIdentity:
    images = _decode(payload.images)
    return LinkIdentity(
        **service.enrol_selfie(db, token, images, delete_drive=service.drive_deleter(db))
    )


@router.post(
    "/interviews/join/{token}/identity/decline",
    response_model=LinkIdentity,
    summary="The candidate says no to face matching, or withdraws; any template is deleted",
)
def decline(token: str, db: Session = Depends(get_db)) -> LinkIdentity:
    current = service.link_status(db, token)
    decision = "withdrawn" if current["consent"] == "given" else "declined"
    return LinkIdentity(**service.refuse(db, token, decision, service.drive_deleter(db)))


# --- the interviewer -------------------------------------------------------------------


@router.get(
    "/interviews/{interview_id}/identity",
    response_model=IdentityOut,
    summary="Who the person in the call is, as far as the face match or the interviewer can tell",
)
def get_identity(
    interview_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(require_interviewer),
) -> IdentityOut:
    return IdentityOut(**service.current(db, interview_id))


@router.post(
    "/interviews/{interview_id}/identity/retry",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Look at the candidate's camera again",
)
def retry_identity(
    interview_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_interviewer),
) -> dict[str, bool]:
    service.request_retry(db, interview_id, user)
    return {"requested": True}


@router.post(
    "/interviews/{interview_id}/identity/decision",
    response_model=IdentityOut,
    summary="The interviewer confirms who this is, or says it is not the candidate",
)
def decide(
    interview_id: int,
    payload: Decision,
    db: Session = Depends(get_db),
    user: User = Depends(require_interviewer),
) -> IdentityOut:
    return IdentityOut(**service.decide_manually(db, interview_id, user, payload.decision))


@router.get(
    "/interviews/{interview_id}/profile",
    response_model=Profile,
    summary="The candidate's profile for the interview room, once their identity is confirmed",
)
def get_profile(
    interview_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(require_interviewer),
) -> Profile:
    return Profile(**service.profile(db, interview_id))
