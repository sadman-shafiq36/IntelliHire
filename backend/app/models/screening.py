from datetime import datetime

from sqlalchemy import (
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.models.enums import (
    RequirementMatchStatus,
    ScoreDimensionName,
    ScreeningStatus,
)


class Screening(Base, TimestampMixin):
    __tablename__ = "screenings"
    __table_args__ = (
        # one verdict per candidate per job, re-screening updates it
        UniqueConstraint("job_id", "candidate_id", name="uq_screening_job_candidate"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    candidate_id: Mapped[int] = mapped_column(
        ForeignKey("candidates.id", ondelete="CASCADE"), index=True, nullable=False
    )
    cv_document_id: Mapped[int | None] = mapped_column(
        ForeignKey("cv_documents.id", ondelete="SET NULL")
    )

    status: Mapped[ScreeningStatus] = mapped_column(
        Enum(ScreeningStatus, native_enum=False, length=20),
        nullable=False,
        default=ScreeningStatus.PENDING,
    )
    # 0-100, drives the ranking
    fulfilment_percent: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    rank: Mapped[int | None] = mapped_column(Integer)
    summary: Mapped[str | None] = mapped_column(Text)
    # so the excluded view can say why
    exclusion_reason: Mapped[str | None] = mapped_column(String(500))

    requirement_results: Mapped[list["ScreeningRequirementResult"]] = relationship(
        back_populates="screening", cascade="all, delete-orphan"
    )


class ScreeningRequirementResult(Base, TimestampMixin):
    # evidence_quote must be copied verbatim from CvExtraction.raw_text

    __tablename__ = "screening_requirement_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    screening_id: Mapped[int] = mapped_column(
        ForeignKey("screenings.id", ondelete="CASCADE"), index=True, nullable=False
    )
    job_requirement_id: Mapped[int] = mapped_column(
        ForeignKey("job_requirements.id", ondelete="CASCADE"), nullable=False
    )

    status: Mapped[RequirementMatchStatus] = mapped_column(
        Enum(RequirementMatchStatus, native_enum=False, length=20), nullable=False
    )
    evidence_quote: Mapped[str | None] = mapped_column(Text)
    reasoning: Mapped[str | None] = mapped_column(Text)
    # 0-1
    confidence: Mapped[float | None] = mapped_column(Float)

    screening: Mapped["Screening"] = relationship(back_populates="requirement_results")


class CandidateScore(Base, TimestampMixin):
    # reproducible: temperature 0, prompt version + model recorded, written once.
    # reading a score never re-runs the llm

    __tablename__ = "candidate_scores"
    __table_args__ = (UniqueConstraint("job_id", "candidate_id", name="uq_score_job_candidate"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    candidate_id: Mapped[int] = mapped_column(
        ForeignKey("candidates.id", ondelete="CASCADE"), index=True, nullable=False
    )

    aggregate_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    rank: Mapped[int | None] = mapped_column(Integer)
    is_shortlisted: Mapped[bool] = mapped_column(default=False, nullable=False)

    model_name: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    prompt_version: Mapped[str] = mapped_column(String(40), nullable=False, default="v1")
    computed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    dimensions: Mapped[list["ScoreDimension"]] = relationship(
        back_populates="score", cascade="all, delete-orphan", order_by="ScoreDimension.id"
    )


class ScoreDimension(Base, TimestampMixin):
    # weight kept on the row so the aggregate can be checked by hand

    __tablename__ = "score_dimensions"

    id: Mapped[int] = mapped_column(primary_key=True)
    score_id: Mapped[int] = mapped_column(
        ForeignKey("candidate_scores.id", ondelete="CASCADE"), index=True, nullable=False
    )

    dimension: Mapped[ScoreDimensionName] = mapped_column(
        Enum(ScoreDimensionName, native_enum=False, length=30), nullable=False
    )
    raw_score: Mapped[float] = mapped_column(Float, nullable=False)  # 0-100
    weight: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    justification: Mapped[str | None] = mapped_column(Text)

    score: Mapped["CandidateScore"] = relationship(back_populates="dimensions")
