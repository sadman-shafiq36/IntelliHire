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


class ScreeningState(BaseGraphState, total=False):
    # inputs
    cv_document_id: int
    file_path: str
    job: dict[str, Any]
    requirements: list[dict[str, Any]]
    # read_cv
    raw_text: str
    page_count: int
    used_ocr: bool
    language: str
    # extract: identity goes to the candidate record, profile is what screening uses
    identity: dict[str, str]
    profile: dict[str, Any]
    redacted_text: str
    # match / verify loop
    attempts: int
    candidate_matches: list[dict[str, Any]]
    matches: list[dict[str, Any]]
    feedback: list[str]
    match_failed: bool
    summary: str
    model_name: str
    # decide: screened | excluded | unreadable | not_english | failed
    outcome: str
    reason: str | None
    fulfilment: float
    warnings: list[str]


class ScoringState(BaseGraphState, total=False):
    # inputs: the screening's verified results and the redacted cv text, never identity
    candidate_id: int
    job: dict[str, Any]
    requirements: list[dict[str, Any]]
    results: list[dict[str, Any]]
    cv_text: str
    years: float | None
    keywords: list[dict[str, Any]]
    description_vector: list[float]
    # evidence_scores / jd_match
    base: dict[str, dict[str, Any]]
    applicable: list[str]
    match: dict[str, Any]
    # assess / verify loop
    attempts: int
    answers: list[dict[str, Any]]
    checked: dict[str, dict[str, Any]]
    feedback: list[str]
    assess_failed: bool
    model_name: str
    # combine: scored | failed
    outcome: str
    reason: str | None
    dimensions: dict[str, dict[str, Any]]
    warnings: list[str]
