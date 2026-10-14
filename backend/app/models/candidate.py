# all personal data. every fk cascades from candidates so deleting one removes
# everything derived from them. identity stays on Candidate, never in
# CvExtraction.structured, so scoring never sees demographics


from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    false,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.models.enums import ExtractionStatus


class Candidate(Base, TimestampMixin):
    # not a User - most candidates never sign in. user_id is set only if they do

    __tablename__ = "candidates"

    id: Mapped[int] = mapped_column(primary_key=True)

    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    # a cv uploaded in bulk may not contain an email address
    email: Mapped[str | None] = mapped_column(String(320), index=True)
    phone: Mapped[str | None] = mapped_column(String(50))
    location: Mapped[str | None] = mapped_column(String(160))

    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    cv_documents: Mapped[list["CvDocument"]] = relationship(
        back_populates="candidate", cascade="all, delete-orphan"
    )
    applications: Mapped[list["Application"]] = relationship(
        back_populates="candidate", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Candidate id={self.id}>"


class Application(Base, TimestampMixin):
    __tablename__ = "applications"
    __table_args__ = (UniqueConstraint("job_id", "external_response_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    candidate_id: Mapped[int] = mapped_column(
        ForeignKey("candidates.id", ondelete="CASCADE"), index=True, nullable=False
    )
    job_post_id: Mapped[int | None] = mapped_column(ForeignKey("job_posts.id", ondelete="SET NULL"))

    # "google_form" or "direct_upload"
    source: Mapped[str] = mapped_column(String(40), nullable=False, default="google_form")
    cover_note: Mapped[str | None] = mapped_column(Text)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # google form response id, so a sync never imports the same application twice
    external_response_id: Mapped[str | None] = mapped_column(String(120))
    # other answers from the form (years of experience, linkedin url, ...)
    answers: Mapped[dict | None] = mapped_column(JSON)
    consent_given: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    cv_document_id: Mapped[int | None] = mapped_column(
        ForeignKey("cv_documents.id", ondelete="SET NULL")
    )

    candidate: Mapped["Candidate"] = relationship(back_populates="applications")


class CvDocument(Base, TimestampMixin):
    # file lives under STORAGE_DIR. status + failure_reason let a batch skip bad files

    __tablename__ = "cv_documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    # empty until the cv has been read, for bulk uploads the person comes from the cv itself
    candidate_id: Mapped[int | None] = mapped_column(
        ForeignKey("candidates.id", ondelete="CASCADE"), index=True
    )
    job_id: Mapped[int | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), index=True
    )

    original_filename: Mapped[str] = mapped_column(String(300), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(500), nullable=False)
    content_type: Mapped[str] = mapped_column(String(120), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)

    status: Mapped[ExtractionStatus] = mapped_column(
        Enum(ExtractionStatus, native_enum=False, length=20),
        nullable=False,
        default=ExtractionStatus.PENDING,
    )
    # shown to the user, keep it non-technical
    failure_reason: Mapped[str | None] = mapped_column(String(500))
    used_ocr: Mapped[bool] = mapped_column(default=False, nullable=False)
    # "google_form" or "upload"
    source: Mapped[str] = mapped_column(
        String(40), nullable=False, default="google_form", server_default="google_form"
    )
    # detected language of the text, screening only runs on english
    language: Mapped[str | None] = mapped_column(String(20))
    uploaded_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    candidate: Mapped["Candidate"] = relationship(back_populates="cv_documents")
    extraction: Mapped["CvExtraction | None"] = relationship(
        back_populates="cv_document", cascade="all, delete-orphan", uselist=False
    )


class CvExtraction(Base, TimestampMixin):
    # raw_text is kept so evidence quotes can be checked against the original

    __tablename__ = "cv_extractions"

    id: Mapped[int] = mapped_column(primary_key=True)
    cv_document_id: Mapped[int] = mapped_column(
        ForeignKey("cv_documents.id", ondelete="CASCADE"),
        index=True,
        unique=True,
        nullable=False,
    )

    raw_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    structured: Mapped[dict | None] = mapped_column(JSON)
    total_years_experience: Mapped[float | None] = mapped_column()
    page_count: Mapped[int | None] = mapped_column(Integer)

    cv_document: Mapped["CvDocument"] = relationship(back_populates="extraction")
