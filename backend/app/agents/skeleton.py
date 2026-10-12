# reference graph for the real ones. no llm call, so it runs without an api key.
#
#   validate --(ok)------> transform --> finalise --> END
#       \--(invalid)-----------------> finalise

from langgraph.graph import END, StateGraph

from app.agents.state import SkeletonState
from app.logging_config import get_logger

log = get_logger(__name__)

MAX_RETRIES = 2


def validate_node(state: SkeletonState) -> dict:
    # report into state instead of raising, raising would lose the checkpoint
    text = (state.get("input_text") or "").strip()
    if not text:
        return {
            "validated": False,
            "errors": [
                {
                    "node": "validate",
                    "code": "empty_input",
                    "message": "No input text was supplied.",
                    "item_ref": None,
                }
            ],
        }
    return {"validated": True}


def transform_node(state: SkeletonState) -> dict:
    # stand-in for an llm call
    text = (state.get("input_text") or "").strip()
    return {"transformed_text": text.upper()}


def finalise_node(state: SkeletonState) -> dict:
    # always reached, so callers get a result even when validation failed
    succeeded = bool(state.get("validated")) and bool(state.get("transformed_text"))
    return {
        "is_complete": True,
        "result": {
            "ok": succeeded,
            "output": state.get("transformed_text", ""),
            "error_count": len(state.get("errors", [])),
        },
    }


def route_after_validate(state: SkeletonState) -> str:
    return "transform" if state.get("validated") else "finalise"


def build_skeleton_graph(checkpointer=None):
    # no checkpointer in tests, so they don't need a database
    builder = StateGraph(SkeletonState)

    builder.add_node("validate", validate_node)
    builder.add_node("transform", transform_node)
    builder.add_node("finalise", finalise_node)

    builder.set_entry_point("validate")
    builder.add_conditional_edges(
        "validate",
        route_after_validate,
        {"transform": "transform", "finalise": "finalise"},
    )
    builder.add_edge("transform", "finalise")
    builder.add_edge("finalise", END)

    return builder.compile(checkpointer=checkpointer)
