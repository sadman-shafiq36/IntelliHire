# face identification at interview. biometric data is the most sensitive thing stored:
# only a candidate who said yes has a face reference, it is an encrypted template (never
# a picture), and it is deleted a few days after their interview. every row cascades
# from the candidate or the interview

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class BiometricConsent(Base, TimestampMixin):
    # append-only: every yes, no and withdrawal, with the wording version they saw

    __tablename__ = "biometric_consents"

    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(
        ForeignKey("candidates.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # given | declined | withdrawn
    decision: Mapped[str] = mapped_column(String(20), nullable=False)
    # form | join_page
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    text_version: Mapped[str] = mapped_column(String(20), nullable=False)


class FaceReference(Base, TimestampMixin):
    # one per candidate. a form photo waits in google drive (drive_file_id) until an
    # interview is booked, then becomes a template and the photo is deleted

    __tablename__ = "face_references"

    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(
        ForeignKey("candidates.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    # pending | ready | unusable
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    # form | selfie
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    drive_file_id: Mapped[str | None] = mapped_column(String(200))
    # no_face | several_faces | too_small | unreadable when unusable
    reason: Mapped[str | None] = mapped_column(String(40))
    embedding: Mapped[bytes | None] = mapped_column(LargeBinary)
    model_name: Mapped[str | None] = mapped_column(String(60))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class IdentityCheck(Base, TimestampMixin):
    # who the person who joined the interview is, as far as the face match or the
    # interviewer can tell. the latest row is the current answer; no image or template here

    __tablename__ = "identity_checks"

    id: Mapped[int] = mapped_column(primary_key=True)
    interview_id: Mapped[int] = mapped_column(
        ForeignKey("interviews.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # confirmed | low_confidence | mismatch | not_identified | manually_confirmed | rejected
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    # face | manual
    method: Mapped[str] = mapped_column(String(20), nullable=False)
    # no_consent | no_reference | no_face | several_faces | too_small | no_match | other
    reason: Mapped[str | None] = mapped_column(String(40))
    score: Mapped[float | None] = mapped_column(Float)
    # how far the claimed candidate is ahead of the best other reference
    margin: Mapped[float | None] = mapped_column(Float)
    decided_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
