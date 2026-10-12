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
