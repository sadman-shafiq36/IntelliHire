from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import (
    ChatMessageStatus,
    ChatRole,
    DescriptionSource,
    JobStatus,
    RequirementCategory,
)


class RequirementIn(BaseModel):
    # id is set when editing an existing requirement, so links to it survive
    id: int | None = None
    text: str = Field(min_length=2, max_length=500)
    category: RequirementCategory = RequirementCategory.TECHNICAL_SKILL
    is_mandatory: bool = False
    weight: float = Field(default=1.0, ge=0.1, le=10)

    @field_validator("text")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()


class RequirementOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    text: str
    category: RequirementCategory
    is_mandatory: bool
    weight: float


class JobIn(BaseModel):
    title: str = Field(min_length=2, max_length=200)
    department: str | None = Field(default=None, max_length=120)
    location: str | None = Field(default=None, max_length=160)
    employment_type: str | None = Field(default=None, max_length=60)
    min_years_experience: int = Field(default=0, ge=0, le=50)
    # free-text selection criteria and context for the jd agent
    summary: str | None = Field(default=None, max_length=4000)
    requirements: list[RequirementIn] = Field(min_length=1, max_length=50)

    @field_validator("title")
    @classmethod
    def _strip_title(cls, value: str) -> str:
        return value.strip()

    @field_validator("department", "location", "employment_type", "summary")
    @classmethod
    def _blank_to_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None


class PolicyCitation(BaseModel):
    chunk_id: str
    policy_id: int
    policy_title: str
    excerpt: str
    used_for: list[str] = []


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    department: str | None
    location: str | None
    employment_type: str | None
    min_years_experience: int
    summary: str | None
    status: JobStatus
    description_md: str | None
    policy_citations: list[PolicyCitation] | None
    requirements: list[RequirementOut]
    created_at: datetime
    updated_at: datetime


class JobListItem(BaseModel):
    id: int
    title: str
    department: str | None
    location: str | None
    status: JobStatus
    requirement_count: int
    mandatory_count: int
    has_description: bool
    updated_at: datetime


class DescriptionIn(BaseModel):
    description_md: str = Field(min_length=20, max_length=20000)


class GenerateResponse(BaseModel):
    background_job_id: str


class VersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    version: int
    source: DescriptionSource
    restored_from: int | None
    description_md: str
    created_at: datetime


class ChatMessageIn(BaseModel):
    message: str = Field(min_length=2, max_length=2000)


class ChatMessageOut(BaseModel):
    id: int
    role: ChatRole
    content: str
    status: ChatMessageStatus
    # the version this reply produced, if it changed the description
    version: int | None
    warnings: list[str]
    created_at: datetime


class ChatResponse(BaseModel):
    background_job_id: str
    message: ChatMessageOut
