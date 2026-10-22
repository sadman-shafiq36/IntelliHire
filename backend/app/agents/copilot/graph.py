# suggesting what to ask next in an interview
#
#   plan -> retrieve -> suggest -> verify -> finalise -> END
#
#   plan    -> finalise   the job has no requirements to ask about
#   suggest -> finalise   the llm failed
#   verify  -> suggest    fewer suggestions than asked for passed the checks, attempts left

from collections.abc import Callable
from typing import Any

from langgraph.graph import END, StateGraph

from app.agents.copilot import checks
from app.agents.copilot.llm import Suggester, llm_suggester
from app.agents.state import CopilotState
from app.config import settings
from app.errors import AppError
from app.rag.policy_store import PolicyChunk
from app.services import ai
from app.tools.reference_search import search_references

MAX_SUGGEST_ATTEMPTS = 2

Embedder = Callable[[list[str]], list[list[float]]]
Retriever = Callable[[list[str]], list[PolicyChunk]]


def make_plan(embedder: Embedder, threshold: float):
    def plan(state: CopilotState) -> dict:
        requirements = state["requirements"]
        if not requirements:
            return {
                "outcome": "nothing",
                "reason": "This job has no requirements to ask about. Add some to its profile.",
            }
        update: dict[str, Any] = {}
        try:
            talked = checks.discussed(
                requirements, checks.exchanges(state.get("turns") or []), embedder, threshold
            )
        except AppError as exc:
            talked = None
            update["errors"] = [_err("plan", exc)]
            update["warnings"] = ["Couldn't work out which requirements were already discussed."]
        targets = checks.plan_targets(requirements, state.get("screening") or {}, talked)
        queries = [t["text"] for t in targets]
        if state.get("focus"):
            queries.append(state["focus"])
        return {**update, "targets": targets, "queries": queries}

    return plan


def make_retrieve(retriever: Retriever):
    def retrieve(state: CopilotState) -> dict:
        try:
            hits = retriever(state["queries"])
        except AppError as exc:
            return {
                "passages": [],
                "errors": [_err("retrieve", exc)],
                "warnings": [
                    "The reference material couldn't be searched, so no reference answers."
                ],
            }
        return {
            "passages": [
                {"chunk_id": h.chunk_id, "title": h.policy_title, "text": h.text} for h in hits
            ]
        }

    return retrieve


def make_suggest(suggester: Suggester):
    def suggest(state: CopilotState) -> dict:
        attempts = state.get("attempts", 0) + 1
        payload = {
            "job": state["job"],
            "targets": state["targets"],
            "dimension_scores": state.get("dimension_scores") or {},
            "turns": state.get("turns") or [],
            "already_asked": _asked(state),
            "focus": state.get("focus"),
            "passages": state.get("passages") or [],
            "cv_text": state.get("cv_text") or "",
            "feedback": state.get("feedback", []),
        }
        try:
            result, model = suggester(payload)
        except AppError as exc:
            return {"attempts": attempts, "suggest_failed": True, "errors": [_err("suggest", exc)]}
        return {
            "attempts": attempts,
            "suggest_failed": False,
            "answers": result.get("suggestions", []),
            "model_name": model,
        }

    return suggest


def verify(state: CopilotState) -> dict:
    earlier = state.get("suggestions") or []
    kept, problems = checks.verify_suggestions(
        state.get("answers") or [],
        state["targets"],
        state.get("cv_text") or "",
        state.get("turns") or [],
        state.get("passages") or [],
        _asked(state),
    )
    # a retry adds to what already passed instead of starting again
    merged = (earlier + kept)[: checks.SUGGESTION_COUNT]
    if len(merged) < checks.SUGGESTION_COUNT:
        problems.append(
            f"Give {checks.SUGGESTION_COUNT - len(merged)} more question(s) that follow the rules."
        )
    return {"suggestions": merged, "feedback": problems}


def finalise(state: CopilotState) -> dict:
    if state.get("outcome") == "nothing":
        return {}
    if state.get("suggestions"):
        return {"outcome": "suggested"}
    if state.get("suggest_failed"):
        return {
            "outcome": "failed",
            "reason": "The AI service is busy or unavailable right now. Try again in a moment.",
        }
    return {
        "outcome": "nothing",
        "reason": "None of the AI's suggestions passed the checks. Try again, or add a focus.",
    }


def _asked(state: CopilotState) -> list[str]:
    return list(state.get("already_asked") or []) + [
        s["question"] for s in state.get("suggestions") or []
    ]


def after_plan(state: CopilotState) -> str:
    return "finalise" if state.get("outcome") == "nothing" else "retrieve"


def after_suggest(state: CopilotState) -> str:
    return "finalise" if state.get("suggest_failed") else "verify"


def after_verify(state: CopilotState) -> str:
    short = len(state.get("suggestions") or []) < checks.SUGGESTION_COUNT
    if short and state["attempts"] < MAX_SUGGEST_ATTEMPTS:
        return "suggest"
    return "finalise"


def build_copilot_graph(
    checkpointer: Any = None,
    suggester: Suggester = llm_suggester,
    embedder: Embedder = ai.embed_texts,
    retriever: Retriever = search_references,
    discussed_threshold: float | None = None,
):
    threshold = (
        settings.copilot_discussed_similarity
        if discussed_threshold is None
        else discussed_threshold
    )
    builder = StateGraph(CopilotState)
    builder.add_node("plan", make_plan(embedder, threshold))
    builder.add_node("retrieve", make_retrieve(retriever))
    builder.add_node("suggest", make_suggest(suggester))
    builder.add_node("verify", verify)
    builder.add_node("finalise", finalise)

    builder.set_entry_point("plan")
    builder.add_conditional_edges(
        "plan", after_plan, {"retrieve": "retrieve", "finalise": "finalise"}
    )
    builder.add_edge("retrieve", "suggest")
    builder.add_conditional_edges(
        "suggest", after_suggest, {"verify": "verify", "finalise": "finalise"}
    )
    builder.add_conditional_edges(
        "verify", after_verify, {"suggest": "suggest", "finalise": "finalise"}
    )
    builder.add_edge("finalise", END)
    return builder.compile(checkpointer=checkpointer)


def _err(node: str, exc: AppError) -> dict:
    return {"node": node, "code": type(exc).__name__, "message": exc.message, "item_ref": None}
