# checking the claims a cv makes against what the candidate said in the interview
#
#   extract -> check_claims -> compare -> check_verdicts -> finalise
#
#   extract        -> finalise        no cv text, the candidate said nothing, or the llm failed
#   check_claims   -> extract         quotes not in the cv, attempts left
#   check_claims   -> finalise        no claim could be quoted from the cv
#   compare        -> finalise        the llm failed (every claim is then "not checked")
#   check_verdicts -> compare         missing or unproven verdicts, attempts left

from typing import Any

from langgraph.graph import END, StateGraph

from app.agents.claims import checks
from app.agents.claims.llm import Comparer, Extractor, llm_comparer, llm_extractor
from app.agents.state import ClaimsState
from app.errors import AppError

MAX_ATTEMPTS = 2


def make_extract(extractor: Extractor):
    def extract(state: ClaimsState) -> dict:
        if not (state.get("cv_text") or "").strip():
            return {"outcome": "empty", "reason": "There is no CV text to check claims against."}
        if not any(t["speaker_role"] == "candidate" for t in state.get("turns") or []):
            return {"outcome": "empty", "reason": "The candidate said nothing in this transcript."}
        attempts = state.get("extract_attempts", 0) + 1
        try:
            result, model = extractor(
                {
                    "job": state["job"],
                    "requirements": state["requirements"],
                    "cv_text": state["cv_text"],
                    "feedback": state.get("extract_feedback", []),
                }
            )
        except AppError as exc:
            return {
                "extract_attempts": attempts,
                "extract_failed": True,
                "errors": [_err("extract", exc)],
            }
        return {
            "extract_attempts": attempts,
            "extract_failed": False,
            "raw_claims": result.get("claims", []),
            "model_name": model,
        }

    return extract


def check_claims(state: ClaimsState) -> dict:
    kept, problems = checks.check_claims(state.get("raw_claims") or [], state["cv_text"])
    # a retry keeps the better of the two lists
    if len(kept) < len(state.get("claims") or []):
        kept = state["claims"]
    return {"claims": kept, "extract_feedback": problems}


def make_compare(comparer: Comparer):
    def compare(state: ClaimsState) -> dict:
        attempts = state.get("compare_attempts", 0) + 1
        try:
            result, model = comparer(
                {
                    "claims": state["claims"],
                    "turns": state["turns"],
                    "feedback": state.get("compare_feedback", []),
                }
            )
        except AppError as exc:
            return {
                "compare_attempts": attempts,
                "compare_failed": True,
                "errors": [_err("compare", exc)],
            }
        return {
            "compare_attempts": attempts,
            "compare_failed": False,
            "raw_verdicts": result.get("verdicts", []),
            "model_name": model,
        }

    return compare


def check_verdicts(state: ClaimsState) -> dict:
    found, problems = checks.check_verdicts(
        state.get("raw_verdicts") or [], state["claims"], state["turns"]
    )
    # a retry keeps what already passed and fills in the rest
    merged = {**found, **(state.get("verdicts") or {})}
    missing = [i for i in range(len(state["claims"])) if str(i) not in merged]
    if missing:
        problems.append(f"Give a verdict for every claim; missing: {', '.join(map(str, missing))}.")
    return {"verdicts": merged, "compare_feedback": problems}


def finalise(state: ClaimsState) -> dict:
    if state.get("outcome"):
        return {}
    if state.get("extract_failed") and not state.get("claims"):
        return {
            "outcome": "failed",
            "reason": "The AI service is busy or unavailable right now. Try again in a moment.",
        }
    if not state.get("claims"):
        return {"outcome": "empty", "reason": "No checkable claims could be quoted from the CV."}
    unchecked = len(state["claims"]) - len(state.get("verdicts") or {})
    warnings = []
    if unchecked:
        warnings.append(f"{unchecked} claim(s) couldn't be checked against the transcript.")
    return {"outcome": "done", "warnings": warnings}


def after_extract(state: ClaimsState) -> str:
    if state.get("outcome") or (state.get("extract_failed") and not state.get("claims")):
        return "finalise"
    return "check_claims"


def after_check_claims(state: ClaimsState) -> str:
    if state.get("extract_feedback") and state["extract_attempts"] < MAX_ATTEMPTS:
        return "extract"
    return "compare" if state.get("claims") else "finalise"


def after_compare(state: ClaimsState) -> str:
    return "finalise" if state.get("compare_failed") else "check_verdicts"


def after_check_verdicts(state: ClaimsState) -> str:
    if state.get("compare_feedback") and state["compare_attempts"] < MAX_ATTEMPTS:
        return "compare"
    return "finalise"


def build_claims_graph(
    checkpointer: Any = None,
    extractor: Extractor = llm_extractor,
    comparer: Comparer = llm_comparer,
):
    builder = StateGraph(ClaimsState)
    builder.add_node("extract", make_extract(extractor))
    builder.add_node("check_claims", check_claims)
    builder.add_node("compare", make_compare(comparer))
    builder.add_node("check_verdicts", check_verdicts)
    builder.add_node("finalise", finalise)

    builder.set_entry_point("extract")
    builder.add_conditional_edges(
        "extract", after_extract, {"check_claims": "check_claims", "finalise": "finalise"}
    )
    builder.add_conditional_edges(
        "check_claims",
        after_check_claims,
        {"extract": "extract", "compare": "compare", "finalise": "finalise"},
    )
    builder.add_conditional_edges(
        "compare",
        after_compare,
        {"check_verdicts": "check_verdicts", "finalise": "finalise"},
    )
    builder.add_conditional_edges(
        "check_verdicts",
        after_check_verdicts,
        {"compare": "compare", "finalise": "finalise"},
    )
    builder.add_edge("finalise", END)
    return builder.compile(checkpointer=checkpointer)


def _err(node: str, exc: AppError) -> dict:
    return {"node": node, "code": type(exc).__name__, "message": exc.message, "item_ref": None}
