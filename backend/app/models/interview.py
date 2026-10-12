from datetime import datetime

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.models.enums import InterviewStatus, SpeakerRole


class Interview(Base, TimestampMixin):
    # meet_url is pasted in by the interviewer, we can't create meet spaces
    # without a workspace account (see PROJECT/decisions.md)

    __tablename__ = "interviews"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    candidate_id: Mapped[int] = mapped_column(
        ForeignKey("candidates.id", ondelete="CASCADE"), index=True, nullable=False
    )
    interviewer_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    scheduled_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scheduled_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    timezone_name: Mapped[str] = mapped_column(String(60), nullable=False, default="UTC")

    meet_url: Mapped[str | None] = mapped_column(String(500))
    # the candidate's only credential, must not be sequential
    join_token: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)

    status: Mapped[InterviewStatus] = mapped_column(
        Enum(InterviewStatus, native_enum=False, length=20),
        nullable=False,
        default=InterviewStatus.SCHEDULED,
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancellation_reason: Mapped[str | None] = mapped_column(String(500))

    participants: Mapped[list["InterviewParticipant"]] = relationship(
        back_populates="interview", cascade="all, delete-orphan"
    )
    turns: Mapped[list["TranscriptTurn"]] = relationship(
        back_populates="interview",
        cascade="all, delete-orphan",
        order_by="TranscriptTurn.sequence",
    )


class InterviewParticipant(Base, TimestampMixin):
    # separate rows so reconnects after a dropped connection are kept

    __tablename__ = "interview_participants"

    id: Mapped[int] = mapped_column(primary_key=True)
    interview_id: Mapped[int] = mapped_column(
        ForeignKey("interviews.id", ondelete="CASCADE"), index=True, nullable=False
    )

    role: Mapped[SpeakerRole] = mapped_column(
        Enum(SpeakerRole, native_enum=False, length=20), nullable=False
    )
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    display_label: Mapped[str] = mapped_column(String(120), nullable=False, default="")

    joined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    left_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    interview: Mapped["Interview"] = relationship(back_populates="participants")


class TranscriptTurn(Base, TimestampMixin):
    # speaker comes from which websocket the audio arrived on, not diarisation.
    # start_offset_ms is for citing evidence, spoken_at is for display

    __tablename__ = "transcript_turns"
    __table_args__ = (Index("ix_turn_interview_sequence", "interview_id", "sequence"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    interview_id: Mapped[int] = mapped_column(
        ForeignKey("interviews.id", ondelete="CASCADE"), nullable=False
    )

    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    speaker_role: Mapped[SpeakerRole] = mapped_column(
        Enum(SpeakerRole, native_enum=False, length=20), nullable=False
    )
    speaker_label: Mapped[str] = mapped_column(String(120), nullable=False, default="")

    text: Mapped[str] = mapped_column(Text, nullable=False)
    start_offset_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    end_offset_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    spoken_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # analysis discounts turns the speech service couldn't transcribe cleanly
    is_uncertain: Mapped[bool] = mapped_column(default=False, nullable=False)

    interview: Mapped["Interview"] = relationship(back_populates="turns")
