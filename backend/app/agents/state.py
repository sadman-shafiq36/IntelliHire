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


class CopilotState(BaseGraphState, total=False):
    # inputs: the redacted cv and role-labelled transcript lines, never the candidate's name
    interview_id: int
    job: dict[str, Any]
    requirements: list[dict[str, Any]]
    # requirement id (as str, checkpoints are json) -> screening result
    screening: dict[str, dict[str, Any]]
    dimension_scores: dict[str, float]
    cv_text: str
    turns: list[dict[str, Any]]
    already_asked: list[str]
    focus: str | None
    # plan
    targets: list[dict[str, Any]]
    queries: list[str]
    # retrieve
    passages: list[dict[str, Any]]
    # suggest / verify loop
    attempts: int
    answers: list[dict[str, Any]]
    suggestions: list[dict[str, Any]]
    feedback: list[str]
    suggest_failed: bool
    model_name: str
    # finalise: suggested | nothing | failed
    outcome: str
    reason: str | None
    warnings: Annotated[list[str], operator.add]


class AnalysisState(BaseGraphState, total=False):
    # inputs: role-labelled transcript lines and the job's requirements, never names
    interview_id: int
    job: dict[str, Any]
    requirements: list[dict[str, Any]]
    turns: list[dict[str, Any]]
    # segment / check_segments loop
    segment_attempts: int
    raw_pairs: list[dict[str, Any]]
    pairs: list[dict[str, Any]]
    segment_feedback: list[str]
    segment_failed: bool
    segmented_by: str  # ai | exchanges
    # retrieve
    passages: list[dict[str, Any]]
    # evaluate / check_evaluations loop
    evaluate_attempts: int
    raw_evaluations: list[dict[str, Any]]
    raw_follow_ups: list[dict[str, Any]]
    evaluations: dict[str, dict[str, Any]]
    evaluate_feedback: list[str]
    evaluate_failed: bool
    model_name: str
    # map
    coverage: list[dict[str, Any]]
    follow_ups: list[dict[str, Any]]
    # finalise: analysed | empty | failed
    outcome: str
    reason: str | None
    warnings: Annotated[list[str], operator.add]


class AssessmentState(BaseGraphState, total=False):
    # inputs: stored results of the earlier stages; the redacted cv, never identity
    interview_id: int
    job: dict[str, Any]
    requirements: list[dict[str, Any]]
    cv_score: float | None
    answer_quality: float | None
    pairs: list[dict[str, Any]]
    turns: list[dict[str, Any]]
    cv_text: str
    cv_document_id: int | None
    # compute
    components: dict[str, dict[str, Any]]
    overall: float | None
    position: str
    capped_by: list[str]
    # write / verify loop
    attempts: int
    answer: dict[str, Any]
    summary: str
    findings: list[dict[str, Any]]
    feedback: list[str]
    write_failed: bool
    model_name: str
    # finalise: assessed | failed
    outcome: str
    reason: str | None
    warnings: Annotated[list[str], operator.add]


class SchedulingState(BaseGraphState, total=False):
    # inputs. the candidate's name and address and the join link are not here: they
    # reach the graph in a closure, so checkpoints never hold contact details or a link
    action: str  # schedule | reschedule | cancel
    job_title: str
    interviewer_name: str
    organizer_email: str | None
    # the interviewer's page for this job, put in their calendar event
    interviewer_url: str | None
    time_zone: str
    duration_minutes: int
    now: str
    start: str | None
    preference: str | None
    allow_conflict: bool
    note: str | None
    reason: str | None
    existing: dict[str, Any] | None
    channel: str  # google | smtp
    # plan / interpret / find_slot
    windows: list[list[str]]
    understood: str
    interpret_attempts: int
    feedback: list[str]
    busy: list[dict[str, str]]
    slot_start: str
    slot_end: str
    # book
    event_id: str | None
    event_created: bool
    # draft / check / send
    draft: dict[str, str]
    draft_attempts: int
    used_template: bool
    ics_uid: str
    ics_sequence: int
    message_id: str
    model_name: str
    # outcome: scheduled | rescheduled | cancelled | conflict | no_slot | failed
    outcome: str
    message: str
    warnings: Annotated[list[str], operator.add]
