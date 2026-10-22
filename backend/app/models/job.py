from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
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
    ChatMessageStatus,
    ChatRole,
    DescriptionSource,
    DocumentKind,
    JobStatus,
    PolicyStatus,
    PostStatus,
    RequirementCategory,
)


class CompanyPolicy(Base, TimestampMixin):
    # file stays on disk, text is chunked into chroma, only metadata here.
    # interview reference material shares the table but has its own chroma collection

    __tablename__ = "company_policies"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[DocumentKind] = mapped_column(
        Enum(DocumentKind, native_enum=False, length=20),
        nullable=False,
        default=DocumentKind.POLICY,
        server_default=DocumentKind.POLICY.name,
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(300), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(500), nullable=False)
    content_type: Mapped[str] = mapped_column(String(120), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)

    status: Mapped[PolicyStatus] = mapped_column(
        Enum(PolicyStatus, native_enum=False, length=20),
        nullable=False,
        default=PolicyStatus.UPLOADED,
    )
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failure_reason: Mapped[str | None] = mapped_column(String(500))

    uploaded_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class Job(Base, TimestampMixin):
    # policy_citations = which policy chunks the jd agent used

    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    department: Mapped[str | None] = mapped_column(String(120))
    location: Mapped[str | None] = mapped_column(String(160))
    employment_type: Mapped[str | None] = mapped_column(String(60))
    min_years_experience: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    summary: Mapped[str | None] = mapped_column(Text)
    description_md: Mapped[str | None] = mapped_column(Text)
    # [{"policy_id": 3, "title": "...", "excerpt": "..."}], display only
    policy_citations: Mapped[list | None] = mapped_column(JSON)
    # scoring weights in use, the ai's suggestion with reasons, and the key terms for
    # the description match
    score_profile: Mapped[dict | None] = mapped_column(JSON)

    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, native_enum=False, length=20),
        nullable=False,
        default=JobStatus.DRAFT,
    )
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    requirements: Mapped[list["JobRequirement"]] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
        order_by="JobRequirement.id",
    )
    posts: Mapped[list["JobPost"]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )
    description_versions: Mapped[list["JobDescriptionVersion"]] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
        order_by="JobDescriptionVersion.version",
    )
    description_messages: Mapped[list["JobDescriptionMessage"]] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
        order_by="JobDescriptionMessage.id",
    )


class JobRequirement(Base, TimestampMixin):
    # one row per requirement, e.g. "3+ years of Python"

    __tablename__ = "job_requirements"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), index=True, nullable=False
    )

    text: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[RequirementCategory] = mapped_column(
        Enum(RequirementCategory, native_enum=False, length=30),
        nullable=False,
        default=RequirementCategory.TECHNICAL_SKILL,
    )
    # failing a mandatory requirement excludes the candidate from ranking
    is_mandatory: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # weight inside its category
    weight: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)

    job: Mapped["Job"] = relationship(back_populates="requirements")


class JobPost(Base, TimestampMixin):
    # a job advertised as a linkedin feed post that links to its own google form.
    # applications come in through the form and are pulled in by a sync

    __tablename__ = "job_posts"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), index=True, nullable=False
    )

    channel: Mapped[str] = mapped_column(String(60), nullable=False, default="linkedin")
    status: Mapped[PostStatus] = mapped_column(
        Enum(PostStatus, native_enum=False, length=20),
        nullable=False,
        default=PostStatus.DRAFT,
        server_default=PostStatus.DRAFT.name,
    )
    # left over from the self-hosted job page idea, unused now
    public_slug: Mapped[str | None] = mapped_column(String(80), unique=True, index=True)
    # the post text, editable until it is published
    rendered_body: Mapped[str | None] = mapped_column(Text)
    hashtags: Mapped[list | None] = mapped_column(JSON)
    contact_email: Mapped[str | None] = mapped_column(String(320))

    form_id: Mapped[str | None] = mapped_column(String(120))
    form_url: Mapped[str | None] = mapped_column(String(500))
    form_edit_url: Mapped[str | None] = mapped_column(String(500))
    # linkedin post urn, e.g. urn:li:share:7311...
    external_id: Mapped[str | None] = mapped_column(String(200))
    external_url: Mapped[str | None] = mapped_column(String(500))
    failure_reason: Mapped[str | None] = mapped_column(String(500))

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    job: Mapped["Job"] = relationship(back_populates="posts")


class JobDescriptionVersion(Base, TimestampMixin):
    # every change to a description is a new row, undo = restore an older one

    __tablename__ = "job_description_versions"
    __table_args__ = (UniqueConstraint("job_id", "version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    description_md: Mapped[str] = mapped_column(Text, nullable=False)
    policy_citations: Mapped[list | None] = mapped_column(JSON)
    source: Mapped[DescriptionSource] = mapped_column(
        Enum(DescriptionSource, native_enum=False, length=20), nullable=False
    )
    # for restores, which version was brought back
    restored_from: Mapped[int | None] = mapped_column(Integer)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    job: Mapped["Job"] = relationship(back_populates="description_versions")


class JobDescriptionMessage(Base, TimestampMixin):
    # the chat with the jd agent about one job

    __tablename__ = "job_description_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    role: Mapped[ChatRole] = mapped_column(
        Enum(ChatRole, native_enum=False, length=20), nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[ChatMessageStatus] = mapped_column(
        Enum(ChatMessageStatus, native_enum=False, length=20),
        nullable=False,
        default=ChatMessageStatus.DONE,
    )
    # set on an assistant reply that changed the description
    version_id: Mapped[int | None] = mapped_column(
        ForeignKey("job_description_versions.id", ondelete="SET NULL")
    )
    warnings: Mapped[list | None] = mapped_column(JSON)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    job: Mapped["Job"] = relationship(back_populates="description_messages")
    version: Mapped["JobDescriptionVersion | None"] = relationship()
