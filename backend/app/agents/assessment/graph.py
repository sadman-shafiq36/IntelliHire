# the consolidated assessment of one interviewed candidate
#
#   compute -> write -> verify -> finalise -> END
#
#   compute -> finalise   nothing could be measured
#   write   -> finalise   the llm failed (the numbers are still saved, without text)
#   verify  -> write      findings without evidence or with a verdict, attempts left

from typing import Any

from langgraph.graph import END, StateGraph

from app.agents.assessment import checks
from app.agents.assessment.llm import Writer, llm_writer
from app.agents.state import AssessmentState
from app.errors import AppError

MAX_ATTEMPTS = 2


def compute(state: AssessmentState) -> dict:
    components, overall, position, capped = checks.compute(
        state.get("cv_score"), state.get("answer_quality"), state["requirements"]
    )
    update: dict[str, Any] = {
        "components": components,
        "overall": overall,
        "position": position,
        "capped_by": capped,
    }
    if overall is None:
        update.update(outcome="failed", reason="There are no scores or answers to assess yet.")
    return update


def make_write(writer: Writer):
    def write(state: AssessmentState) -> dict:
        attempts = state.get("attempts", 0) + 1
        by_id = {t["id"]: t for t in state.get("turns") or []}
        payload = {
            "job": state["job"],
            "position": state["position"],
            "overall": state["overall"],
            "capped_by": state.get("capped_by") or [],
            "components": {k: v["score"] for k, v in state["components"].items()},
            "requirements": state["requirements"],
            "pairs": [
                {**p, "answer_turns": [by_id[i] for i in p["answer_turn_ids"] if i in by_id]}
                for p in state.get("pairs") or []
            ],
            "cv_text": state.get("cv_text") or "",
            "feedback": state.get("feedback", []),
        }
        try:
            result, model = writer(payload)
        except AppError as exc:
            return {"attempts": attempts, "write_failed": True, "errors": [_err("write", exc)]}
        return {"attempts": attempts, "write_failed": False, "answer": result, "model_name": model}

    return write


def verify(state: AssessmentState) -> dict:
    summary, findings, problems = checks.check_findings(
        state.get("answer") or {},
        state.get("cv_text") or "",
        state.get("turns") or [],
        state["requirements"],
        state.get("cv_document_id"),
    )
    # a retry keeps the better of the two answers
    if len(findings) < len(state.get("findings") or []):
        findings = state["findings"]
    return {
        "summary": summary or state.get("summary") or "",
        "findings": findings,
        "feedback": problems,
    }


def finalise(state: AssessmentState) -> dict:
    if state.get("outcome") == "failed":
        return {}
    warnings = []
    if state.get("write_failed") and not state.get("findings"):
        warnings.append(
            "The AI service couldn't write the findings, so the report shows the scores only. "
            "Generate it again to add them."
        )
    elif state.get("feedback"):
        warnings.append("Some findings were left out because their evidence couldn't be found.")
    return {"outcome": "assessed", "warnings": warnings}


def after_compute(state: AssessmentState) -> str:
    return "finalise" if state.get("outcome") == "failed" else "write"


def after_write(state: AssessmentState) -> str:
    return "finalise" if state.get("write_failed") else "verify"


def after_verify(state: AssessmentState) -> str:
    if state.get("feedback") and state["attempts"] < MAX_ATTEMPTS:
        return "write"
    return "finalise"


def build_assessment_graph(checkpointer: Any = None, writer: Writer = llm_writer):
    builder = StateGraph(AssessmentState)
    builder.add_node("compute", compute)
    builder.add_node("write", make_write(writer))
    builder.add_node("verify", verify)
    builder.add_node("finalise", finalise)

    builder.set_entry_point("compute")
    builder.add_conditional_edges(
        "compute", after_compute, {"write": "write", "finalise": "finalise"}
    )
    builder.add_conditional_edges(
        "write", after_write, {"verify": "verify", "finalise": "finalise"}
    )
    builder.add_conditional_edges(
        "verify", after_verify, {"write": "write", "finalise": "finalise"}
    )
    builder.add_edge("finalise", END)
    return builder.compile(checkpointer=checkpointer)


def _err(node: str, exc: AppError) -> dict:
    return {"node": node, "code": type(exc).__name__, "message": exc.message, "item_ref": None}
