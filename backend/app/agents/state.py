# one TypedDict per workflow. nodes return only the keys they change.
# keep state plain data (no orm objects) so it can be checkpointed

import operator
from typing import Annotated, Any, TypedDict


class AgentError(TypedDict):
    # item_ref = what failed (cv id, requirement id) so a batch can skip it

    node: str
    code: str
    message: str
    item_ref: str | None


class BaseGraphState(TypedDict, total=False):
    thread_id: str
    # operator.add reducer: returning {"errors": [e]} appends instead of replacing
    errors: Annotated[list[AgentError], operator.add]
    retry_count: int
    is_complete: bool


class SkeletonState(BaseGraphState, total=False):
    input_text: str
    validated: bool
    transformed_text: str
    result: dict[str, Any]


class JDState(BaseGraphState, total=False):
    # inputs
    job_id: int
    profile: dict[str, Any]
    # chat mode: what the interviewer asked, the description it applies to, the
    # recent conversation, and the passages the current description already cites
    request: str | None
    current_md: str | None
    history: list[dict[str, str]]
    prior_chunks: list[dict[str, Any]]
    # retrieval
    queries: list[str]
    policy_chunks: list[dict[str, Any]]
    # drafting loop, feedback is what review found wrong with the last draft
    draft: dict[str, Any] | None
    feedback: list[str]
    attempts: int
    llm_failed: bool
    # outputs
    description_md: str
    citations: list[dict[str, Any]]
    warnings: list[str]
    reply: str
    changed: bool
