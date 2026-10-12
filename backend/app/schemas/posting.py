from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import PostStatus


class JobPostOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    job_id: int
    channel: str
    status: PostStatus
    text: str | None = Field(validation_alias="rendered_body")
    hashtags: list[str] | None
    form_url: str | None
    form_edit_url: str | None
    post_url: str | None = Field(validation_alias="external_url")
    failure_reason: str | None
    posted_at: datetime | None
    closed_at: datetime | None
    last_synced_at: datetime | None


class PostStarted(BaseModel):
    post: JobPostOut
    background_job_id: str


class PostTextIn(BaseModel):
    text: str = Field(min_length=20, max_length=2800)


class SyncStarted(BaseModel):
    background_job_id: str


class ApplicationOut(BaseModel):
    id: int
    candidate_id: int
    full_name: str
    email: str
    phone: str | None
    location: str | None
    source: str
    submitted_at: datetime | None
    years_experience: str | None
    linkedin_url: str | None
    cover_note: str | None
    cv_filename: str | None
    cv_status: str | None
