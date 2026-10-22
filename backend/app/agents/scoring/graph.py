# scoring one screened candidate for one job
#
#   evidence_scores -> jd_match -> assess -> verify -> combine -> END
#
#   jd_match -> combine   the embedding call failed
#   assess   -> combine   the llm failed (keeps the last checked answer if there is one)
#   verify   -> assess    quotes not in the cv or unsupported adjustments, attempts left

from collections.abc import Callable
from typing import Any

from langgraph.graph import END, StateGraph

from app.agents.scoring import checks
from app.agents.scoring.llm import Assessor, llm_assessor
from app.agents.state import ScoringState
from app.errors import AppError
from app.services import ai

MAX_ASSESS_ATTEMPTS = 2

Embedder = Callable[[list[str]], list[list[float]]]


def evidence_scores(state: ScoringState) -> dict:
    base = checks.evidence_scores(
        state["results"],
        state["requirements"],
        state.get("years"),
        state["job"].get("min_years_experience") or 0,
    )
    return {"base": base, "applicable": [d for d in checks.ASSESSED if d in base]}


def make_jd_match(embedder: Embedder):
    def jd_match(state: ScoringState) -> dict:
        try:
            [vector] = embedder([state["cv_text"][: checks.EMBED_MAX_CHARS]])
        except AppError as exc:
            return {"outcome": "failed", "reason": exc.message, "errors": [_err("jd_match", exc)]}
        similarity = checks.cosine(vector, state["description_vector"])
        return {
            "match": checks.description_match(
                state.get("keywords") or [], state["cv_text"], similarity
            )
        }

    return jd_match


def make_assess(assessor: Assessor):
    def assess(state: ScoringState) -> dict:
        attempts = state.get("attempts", 0) + 1
        payload = {
            "job": state["job"],
            "base": state["base"],
            "applicable": state["applicable"],
            "years": state.get("years") or 0,
            "cv_text": state["cv_text"],
            "feedback": state.get("feedback", []),
        }
        try:
            result, model = assessor(payload)
        except AppError as exc:
            update: dict[str, Any] = {
                "attempts": attempts,
                "assess_failed": True,
                "errors": [_err("assess", exc)],
            }
            if not state.get("checked"):
                update.update(outcome="failed", reason=exc.message)
            return update
        return {
            "attempts": attempts,
            "assess_failed": False,
            "answers": result.get("dimensions", []),
            "model_name": model,
        }

    return assess


def verify(state: ScoringState) -> dict:
    checked, problems = checks.verify_assessment(
        state["answers"], state["applicable"], state["cv_text"]
    )
    return {"checked": checked, "feedback": problems}


def combine(state: ScoringState) -> dict:
    if state.get("outcome") == "failed":
        return {}
    settled, warnings = checks.settle_assessment(state.get("checked", {}), state["applicable"])
    if state.get("assess_failed"):
        warnings.insert(0, "The AI service failed on a retry, so the last checked answer was used.")
    return {
        "outcome": "scored",
        "dimensions": checks.combine(state["base"], settled, state.get("match")),
        "warnings": warnings,
    }


def after_jd_match(state: ScoringState) -> str:
    if state.get("outcome") == "failed":
        return "combine"
    return "assess" if state["applicable"] else "combine"


def after_assess(state: ScoringState) -> str:
    return "combine" if state.get("assess_failed") else "verify"


def after_verify(state: ScoringState) -> str:
    if state.get("feedback") and state["attempts"] < MAX_ASSESS_ATTEMPTS:
        return "assess"
    return "combine"


def build_scoring_graph(
    checkpointer: Any = None,
    assessor: Assessor = llm_assessor,
    embedder: Embedder = ai.embed_texts,
):
    builder = StateGraph(ScoringState)
    builder.add_node("evidence_scores", evidence_scores)
    builder.add_node("jd_match", make_jd_match(embedder))
    builder.add_node("assess", make_assess(assessor))
    builder.add_node("verify", verify)
    builder.add_node("combine", combine)

    builder.set_entry_point("evidence_scores")
    builder.add_edge("evidence_scores", "jd_match")
    builder.add_conditional_edges(
        "jd_match", after_jd_match, {"assess": "assess", "combine": "combine"}
    )
    builder.add_conditional_edges(
        "assess", after_assess, {"verify": "verify", "combine": "combine"}
    )
    builder.add_conditional_edges(
        "verify", after_verify, {"assess": "assess", "combine": "combine"}
    )
    builder.add_edge("combine", END)
    return builder.compile(checkpointer=checkpointer)


def _err(node: str, exc: AppError) -> dict:
    return {"node": node, "code": type(exc).__name__, "message": exc.message, "item_ref": None}
