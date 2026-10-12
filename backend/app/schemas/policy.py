from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.enums import PolicyStatus


class PolicyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    original_filename: str
    content_type: str
    size_bytes: int
    status: PolicyStatus
    chunk_count: int
    failure_reason: str | None
    created_at: datetime


class PolicyUploadResponse(BaseModel):
    policy: PolicyOut
    background_job_id: str


class PolicySearchHit(BaseModel):
    chunk_id: str
    policy_id: int
    policy_title: str
    chunk_index: int
    text: str
    score: float
