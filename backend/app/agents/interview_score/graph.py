# the candidate's interview performance score, separate from the cv-based score
#
#   baseline -> judge -> check -> aggregate -> finalise
#
#   baseline -> finalise    nothing was answered
#   judge    -> aggregate   the llm failed (measured dimensions keep their measured score)
#   check    -> judge       unproven or missing judgements, attempts left

from typing import Any

from langgraph.graph import END, StateGraph

from app.agents.interview_score import checks
from app.agents.interview_score.llm import Judge, llm_judge
from app.agents.state import InterviewScoreState
from app.errors import AppError

MAX_ATTEMPTS = 2


def baseline(state: InterviewScoreState) -> dict:
    if not any(p.get("answer_turn_ids") for p in state.get("pairs") or []):
        return {"outcome": "empty", "reason": "The candidate answered no questions to score."}
    return {"baselines": checks.baselines(state["pairs"], state["requirements"])}


def make_judge(judge: Judge):
    def judge_node(state: InterviewScoreState) -> dict:
        attempts = state.get("attempts", 0) + 1
        by_id = {t["id"]: t for t in state["turns"]}
        pairs = [
            {**p, "answer_turns": [by_id[i] for i in p["answer_turn_ids"] if i in by_id]}
            for p in state["pairs"]
            if p.get("answer_turn_ids")
        ]
        try:
            result, model = judge(
                {
                    "job": state["job"],
                    "requirements": state["requirements"],
                    "baselines": state["baselines"],
                    "pairs": pairs,
                    "feedback": state.get("feedback", []),
                }
            )
        except AppError as exc:
            return {"attempts": attempts, "judge_failed": True, "errors": [_err("judge", exc)]}
        return {
            "attempts": attempts,
            "judge_failed": False,
            "raw_judgements": result.get("judgements", []),
            "model_name": model,
        }

    return judge_node


def check(state: InterviewScoreState) -> dict:
    found, problems = checks.check_judgements(
        state.get("raw_judgements") or [], state["baselines"], state["turns"]
    )
    # a retry keeps what already passed and fills in the rest
    return {"dimensions": {**found, **(state.get("dimensions") or {})}, "feedback": problems}


def aggregate(state: InterviewScoreState) -> dict:
    dimensions = checks.fill_missing(state.get("dimensions") or {}, state["baselines"])
    overall, effective = checks.aggregate(dimensions)
    dimensions = {
        name: {
            **d,
            "weight": checks.WEIGHTS[name],
            "effective_weight": effective[name],
            "basis": state["baselines"][name]["basis"],
        }
        for name, d in dimensions.items()
    }
    warnings = []
    if state.get("judge_failed") and not state.get("dimensions"):
        warnings.append(
            "The AI service couldn't judge the answers, so only the measured dimensions are "
            "scored. Score it again to add the rest."
        )
    elif state.get("feedback"):
        warnings.append("Some judgements were left out because their evidence couldn't be found.")
    return {"dimensions": dimensions, "overall": overall, "warnings": warnings}


def finalise(state: InterviewScoreState) -> dict:
    if state.get("outcome"):
        return {}
    if state.get("overall") is None:
        return {"outcome": "failed", "reason": "No dimension could be scored from this interview."}
    return {"outcome": "done"}


def after_baseline(state: InterviewScoreState) -> str:
    return "finalise" if state.get("outcome") else "judge"


def after_judge(state: InterviewScoreState) -> str:
    return "aggregate" if state.get("judge_failed") else "check"


def after_check(state: InterviewScoreState) -> str:
    if state.get("feedback") and state["attempts"] < MAX_ATTEMPTS:
        return "judge"
    return "aggregate"


def build_interview_score_graph(checkpointer: Any = None, judge: Judge = llm_judge):
    builder = StateGraph(InterviewScoreState)
    builder.add_node("baseline", baseline)
    builder.add_node("judge", make_judge(judge))
    builder.add_node("check", check)
    builder.add_node("aggregate", aggregate)
    builder.add_node("finalise", finalise)

    builder.set_entry_point("baseline")
    builder.add_conditional_edges(
        "baseline", after_baseline, {"judge": "judge", "finalise": "finalise"}
    )
    builder.add_conditional_edges(
        "judge", after_judge, {"check": "check", "aggregate": "aggregate"}
    )
    builder.add_conditional_edges(
        "check", after_check, {"judge": "judge", "aggregate": "aggregate"}
    )
    builder.add_edge("aggregate", "finalise")
    builder.add_edge("finalise", END)
    return builder.compile(checkpointer=checkpointer)


def _err(node: str, exc: AppError) -> dict:
    return {"node": node, "code": type(exc).__name__, "message": exc.message, "item_ref": None}
