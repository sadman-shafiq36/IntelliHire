from datetime import datetime

from sqlalchemy import (
    JSON,
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
    AgentRunStatus,
    CoverageStatus,
    EvidenceType,
    FindingKind,
)


class QaPair(Base, TimestampMixin):
    # justification has to cite answer_turn_ids so it stays traceable

    __tablename__ = "qa_pairs"

    id: Mapped[int] = mapped_column(primary_key=True)
    interview_id: Mapped[int] = mapped_column(
        ForeignKey("interviews.id", ondelete="CASCADE"), index=True, nullable=False
    )

    sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    question_text: Mapped[str] = mapped_column(Text, nullable=False)
    answer_text: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # lets the ui jump to the exact moment
    question_turn_ids: Mapped[list | None] = mapped_column(JSON)
    answer_turn_ids: Mapped[list | None] = mapped_column(JSON)

    # set when this question follows up on an earlier one
    follow_up_to_id: Mapped[int | None] = mapped_column(
        ForeignKey("qa_pairs.id", ondelete="SET NULL")
    )

    relevance_score: Mapped[float | None] = mapped_column(Float)  # 0-100
    accuracy_score: Mapped[float | None] = mapped_column(Float)
    completeness_score: Mapped[float | None] = mapped_column(Float)
    articulation_score: Mapped[float | None] = mapped_column(Float)
    justification: Mapped[str | None] = mapped_column(Text)

    requirement_links: Mapped[list["QaRequirementLink"]] = relationship(
        back_populates="qa_pair", cascade="all, delete-orphan"
    )


class QaRequirementLink(Base, TimestampMixin):
    # requirements with no link after the interview were never explored

    __tablename__ = "qa_requirement_links"

    id: Mapped[int] = mapped_column(primary_key=True)
    qa_pair_id: Mapped[int] = mapped_column(
        ForeignKey("qa_pairs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    job_requirement_id: Mapped[int] = mapped_column(
        ForeignKey("job_requirements.id", ondelete="CASCADE"), index=True, nullable=False
    )

    coverage: Mapped[CoverageStatus] = mapped_column(
        Enum(CoverageStatus, native_enum=False, length=25), nullable=False
    )
    note: Mapped[str | None] = mapped_column(Text)

    qa_pair: Mapped["QaPair"] = relationship(back_populates="requirement_links")


class Assessment(Base, TimestampMixin):
    # component_scores stay separate so it's not one opaque number

    __tablename__ = "assessments"
    __table_args__ = (UniqueConstraint("interview_id", name="uq_assessment_interview"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    interview_id: Mapped[int] = mapped_column(
        ForeignKey("interviews.id", ondelete="CASCADE"), index=True, nullable=False
    )
    job_id: Mapped[int] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    candidate_id: Mapped[int] = mapped_column(
        ForeignKey("candidates.id", ondelete="CASCADE"), index=True, nullable=False
    )

    overall_score: Mapped[float | None] = mapped_column(Float)
    # "strong_fit" | "possible_fit" | "not_a_fit"
    overall_position: Mapped[str | None] = mapped_column(String(40))
    component_scores: Mapped[dict | None] = mapped_column(JSON)
    summary: Mapped[str | None] = mapped_column(Text)

    model_name: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    findings: Mapped[list["AssessmentFinding"]] = relationship(
        back_populates="assessment", cascade="all, delete-orphan"
    )


class AssessmentFinding(Base, TimestampMixin):
    # evidence_type + evidence_ref_id point at a cv document or transcript turn

    __tablename__ = "assessment_findings"

    id: Mapped[int] = mapped_column(primary_key=True)
    assessment_id: Mapped[int] = mapped_column(
        ForeignKey("assessments.id", ondelete="CASCADE"), index=True, nullable=False
    )

    kind: Mapped[FindingKind] = mapped_column(
        Enum(FindingKind, native_enum=False, length=25), nullable=False
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)

    evidence_type: Mapped[EvidenceType | None] = mapped_column(
        Enum(EvidenceType, native_enum=False, length=20)
    )
    evidence_ref_id: Mapped[int | None] = mapped_column(Integer)
    evidence_quote: Mapped[str | None] = mapped_column(Text)

    assessment: Mapped["Assessment"] = relationship(back_populates="findings")


class CopilotSuggestion(Base, TimestampMixin):
    # kept so we can see which suggestions the interviewer actually used

    __tablename__ = "copilot_suggestions"

    id: Mapped[int] = mapped_column(primary_key=True)
    interview_id: Mapped[int] = mapped_column(
        ForeignKey("interviews.id", ondelete="CASCADE"), index=True, nullable=False
    )
    job_requirement_id: Mapped[int | None] = mapped_column(
        ForeignKey("job_requirements.id", ondelete="SET NULL")
    )

    question_text: Mapped[str] = mapped_column(Text, nullable=False)
    rationale: Mapped[str | None] = mapped_column(Text)
    reference_answer: Mapped[str | None] = mapped_column(Text)
    sources: Mapped[list | None] = mapped_column(JSON)

    was_used: Mapped[bool] = mapped_column(default=False, nullable=False)


class AgentRun(Base, TimestampMixin):
    # error code only, never the prompt or provider message

    __tablename__ = "agent_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    graph_name: Mapped[str] = mapped_column(String(60), index=True, nullable=False)
    thread_id: Mapped[str] = mapped_column(String(100), index=True, nullable=False)

    status: Mapped[AgentRunStatus] = mapped_column(
        Enum(AgentRunStatus, native_enum=False, length=20),
        nullable=False,
        default=AgentRunStatus.RUNNING,
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(80))

    # ids only, e.g. {"job_id": 3, "cv_count": 20}
    context: Mapped[dict | None] = mapped_column(JSON)
