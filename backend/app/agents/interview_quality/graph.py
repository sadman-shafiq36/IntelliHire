# reviewing how an interview was run: the questions, coverage, speaking time and
# consistency with the job's other interviews, with recommendations
#
#   measure -> review -> check -> finalise
#
#   measure -> finalise   there are no questions to review
#   review  -> finalise   the llm failed (the measures and rule-based advice are still saved)
#   check   -> review     missing or refused reviews, attempts left

from typing import Any

from langgraph.graph import END, StateGraph

from app.agents.interview_quality import checks
from app.agents.interview_quality.llm import Reviewer, llm_reviewer
from app.agents.state import QualityState
from app.errors import AppError

MAX_ATTEMPTS = 2


def measure(state: QualityState) -> dict:
    if not state.get("pairs"):
        return {"outcome": "empty", "reason": "There are no questions in this interview to review."}
    metrics = checks.measure(
        state["turns"],
        state["pairs"],
        state["requirements"],
        state.get("peers") or [],
        state.get("pause_ms", 0),
    )
    return {
        "metrics": metrics,
        "issues": checks.issues(metrics, state["pairs"], state["requirements"]),
    }


def make_review(reviewer: Reviewer):
    def review(state: QualityState) -> dict:
        attempts = state.get("attempts", 0) + 1
        try:
            result, model = reviewer(
                {
                    "job": state["job"],
                    "pairs": state["pairs"],
                    "turns": state["turns"],
                    "metrics": state["metrics"],
                    "issues": state["issues"],
                    "feedback": state.get("feedback", []),
                }
            )
        except AppError as exc:
            return {"attempts": attempts, "review_failed": True, "errors": [_err("review", exc)]}
        return {
            "attempts": attempts,
            "review_failed": False,
            "raw_review": result,
            "model_name": model,
        }

    return review


def check(state: QualityState) -> dict:
    questions, recommendations, summary, problems = checks.check_review(
        state.get("raw_review") or {}, state["pairs"], state["issues"]
    )
    # a retry keeps the reviews that already passed
    merged = {**questions, **(state.get("questions") or {})}
    kept = {r["issue"]: r for r in state.get("recommendations") or [] if r["written_by"] == "ai"}
    recommendations = [
        kept[r["issue"]] if r["written_by"] == "rule" and r["issue"] in kept else r
        for r in recommendations
    ]
    return {
        "questions": merged,
        "recommendations": recommendations,
        "summary": summary or state.get("summary") or "",
        "feedback": problems,
    }


def finalise(state: QualityState) -> dict:
    if state.get("outcome"):
        return {}
    update: dict[str, Any] = {
        "outcome": "done",
        "scores": checks.scores(
            state["metrics"], state.get("questions") or {}, len(state["pairs"])
        ),
    }
    if state.get("review_failed") and not state.get("questions"):
        # the issues found by code still get their plain recommendations
        update["recommendations"] = [
            {
                "issue": i["id"],
                "title": i["title"],
                "evidence": i["evidence"],
                "text": i["default"],
                "written_by": "rule",
            }
            for i in state["issues"]
        ][: checks.MAX_RECOMMENDATIONS]
        update["warnings"] = [
            "The AI service couldn't review the questions, so only the measures and standard "
            "advice are shown. Review it again to add them."
        ]
    elif state.get("feedback"):
        update["warnings"] = ["Some question reviews were left out because they failed the checks."]
    return update


def after_measure(state: QualityState) -> str:
    return "finalise" if state.get("outcome") else "review"


def after_review(state: QualityState) -> str:
    return "finalise" if state.get("review_failed") else "check"


def after_check(state: QualityState) -> str:
    if state.get("feedback") and state["attempts"] < MAX_ATTEMPTS:
        return "review"
    return "finalise"


def build_quality_graph(checkpointer: Any = None, reviewer: Reviewer = llm_reviewer):
    builder = StateGraph(QualityState)
    builder.add_node("measure", measure)
    builder.add_node("review", make_review(reviewer))
    builder.add_node("check", check)
    builder.add_node("finalise", finalise)

    builder.set_entry_point("measure")
    builder.add_conditional_edges(
        "measure", after_measure, {"review": "review", "finalise": "finalise"}
    )
    builder.add_conditional_edges(
        "review", after_review, {"check": "check", "finalise": "finalise"}
    )
    builder.add_conditional_edges(
        "check", after_check, {"review": "review", "finalise": "finalise"}
    )
    builder.add_edge("finalise", END)
    return builder.compile(checkpointer=checkpointer)


def _err(node: str, exc: AppError) -> dict:
    return {"node": node, "code": type(exc).__name__, "message": exc.message, "item_ref": None}
