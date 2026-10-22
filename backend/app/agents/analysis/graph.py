# analysing a finished interview's transcript
#
#   segment -> check_segments -> retrieve -> evaluate -> check_evaluations -> map -> finalise
#
#   segment           -> finalise         nothing was said, or nothing by the candidate
#   check_segments    -> segment          the split broke the rules, attempts left
#   check_segments    -> retrieve         otherwise; with no usable split the plain
#                                         interviewer / candidate exchanges are used
#   evaluate          -> finalise         the llm failed
#   check_evaluations -> evaluate         missing or untraceable evaluations, attempts left

from collections.abc import Callable
from typing import Any

from langgraph.graph import END, StateGraph

from app.agents.analysis import checks
from app.agents.analysis.llm import Evaluator, Segmenter, llm_evaluator, llm_segmenter
from app.agents.state import AnalysisState
from app.errors import AppError
from app.rag.policy_store import PolicyChunk
from app.tools.reference_search import search_references

MAX_ATTEMPTS = 2
MAX_QUERIES = 20

Retriever = Callable[[list[str]], list[PolicyChunk]]


def make_segment(segmenter: Segmenter):
    def segment(state: AnalysisState) -> dict:
        turns = state.get("turns") or []
        if not any(t["speaker_role"] == "candidate" for t in turns):
            return {"outcome": "empty", "reason": "The candidate said nothing in this transcript."}
        attempts = state.get("segment_attempts", 0) + 1
        try:
            result, model = segmenter(
                {"turns": turns, "feedback": state.get("segment_feedback", [])}
            )
        except AppError as exc:
            return {
                "segment_attempts": attempts,
                "segment_failed": True,
                "errors": [_err("segment", exc)],
            }
        return {
            "segment_attempts": attempts,
            "segment_failed": False,
            "raw_pairs": result.get("pairs", []),
            "model_name": model,
        }

    return segment


def check_segments(state: AnalysisState) -> dict:
    if state.get("segment_failed"):
        return {"segment_feedback": ["failed"]}
    pairs, problems, warnings = checks.check_segments(state.get("raw_pairs") or [], state["turns"])
    return {
        "pairs": pairs,
        "segment_feedback": problems,
        "segmented_by": "ai",
        "warnings": [] if problems else warnings,
    }


def use_exchanges(state: AnalysisState) -> dict:
    # the ai's split couldn't be used: fall back to who spoke when, which is always right
    # about the speakers but doesn't split multi-part questions or drop small talk
    kept = state.get("pairs") or []
    if kept and not state.get("segment_failed"):
        return {
            "segmented_by": "ai",
            "warnings": [
                "Some of the AI's question-answer pairs failed the checks and were left out."
            ],
        }
    return {
        "pairs": checks.exchanges_as_pairs(state["turns"]),
        "segmented_by": "exchanges",
        "warnings": ["The AI couldn't split the transcript, so each exchange is one pair."],
    }


def make_retrieve(retriever: Retriever):
    def retrieve(state: AnalysisState) -> dict:
        queries = [p["question"] for p in state["pairs"]][:MAX_QUERIES]
        try:
            hits = retriever(queries)
        except AppError as exc:
            return {
                "passages": [],
                "errors": [_err("retrieve", exc)],
                "warnings": [
                    "Reference material couldn't be searched; accuracy is judged generally."
                ],
            }
        return {
            "passages": [
                {"chunk_id": h.chunk_id, "title": h.policy_title, "text": h.text} for h in hits
            ]
        }

    return retrieve


def make_evaluate(evaluator: Evaluator):
    def evaluate(state: AnalysisState) -> dict:
        attempts = state.get("evaluate_attempts", 0) + 1
        by_id = {t["id"]: t for t in state["turns"]}
        pairs = [
            {**p, "answer_turns": [by_id[i] for i in p["answer_turn_ids"]]} for p in state["pairs"]
        ]
        try:
            result, model = evaluator(
                {
                    "job": state["job"],
                    "requirements": state["requirements"],
                    "pairs": pairs,
                    "passages": state.get("passages") or [],
                    "feedback": state.get("evaluate_feedback", []),
                }
            )
        except AppError as exc:
            return {
                "evaluate_attempts": attempts,
                "evaluate_failed": True,
                "errors": [_err("evaluate", exc)],
            }
        return {
            "evaluate_attempts": attempts,
            "evaluate_failed": False,
            "raw_evaluations": result.get("evaluations", []),
            "raw_follow_ups": result.get("follow_ups", []),
            "model_name": model,
        }

    return evaluate


def check_evaluations(state: AnalysisState) -> dict:
    found, problems = checks.check_evaluations(
        state.get("raw_evaluations") or [],
        state["pairs"],
        state["requirements"],
        state.get("passages") or [],
    )
    # a retry keeps what already passed and fills in the rest
    merged = {**found, **(state.get("evaluations") or {})}
    missing = [i for i in range(len(state["pairs"])) if str(i) not in merged]
    if missing:
        problems.append(f"Evaluate every pair; missing: {', '.join(map(str, missing))}.")
    return {"evaluations": merged, "evaluate_feedback": problems}


def map_requirements(state: AnalysisState) -> dict:
    evaluations = state.get("evaluations") or {}
    covered = checks.coverage(state["requirements"], evaluations)
    asked = [p["question"] for p in state["pairs"]]
    warnings = []
    missing = len(state["pairs"]) - len(evaluations)
    if missing:
        warnings.append(f"{missing} question(s) couldn't be evaluated with traceable evidence.")
    return {
        "coverage": covered,
        "follow_ups": checks.check_follow_ups(state.get("raw_follow_ups") or [], covered, asked),
        "warnings": warnings,
    }


def finalise(state: AnalysisState) -> dict:
    if state.get("outcome") == "empty":
        return {}
    if state.get("evaluate_failed") and not state.get("evaluations"):
        return {
            "outcome": "failed",
            "reason": "The AI service is busy or unavailable right now. Try again in a moment.",
        }
    return {"outcome": "analysed"}


def after_segment(state: AnalysisState) -> str:
    return "finalise" if state.get("outcome") == "empty" else "check_segments"


def after_check_segments(state: AnalysisState) -> str:
    retry = state.get("segment_feedback") and not state.get("segment_failed")
    if retry and state["segment_attempts"] < MAX_ATTEMPTS:
        return "segment"
    return "use_exchanges" if state.get("segment_feedback") else "retrieve"


def after_evaluate(state: AnalysisState) -> str:
    if state.get("evaluate_failed"):
        return "map" if state.get("evaluations") else "finalise"
    return "check_evaluations"


def after_check_evaluations(state: AnalysisState) -> str:
    if state.get("evaluate_feedback") and state["evaluate_attempts"] < MAX_ATTEMPTS:
        return "evaluate"
    return "map"


def build_analysis_graph(
    checkpointer: Any = None,
    segmenter: Segmenter = llm_segmenter,
    evaluator: Evaluator = llm_evaluator,
    retriever: Retriever = search_references,
):
    builder = StateGraph(AnalysisState)
    builder.add_node("segment", make_segment(segmenter))
    builder.add_node("check_segments", check_segments)
    builder.add_node("use_exchanges", use_exchanges)
    builder.add_node("retrieve", make_retrieve(retriever))
    builder.add_node("evaluate", make_evaluate(evaluator))
    builder.add_node("check_evaluations", check_evaluations)
    builder.add_node("map", map_requirements)
    builder.add_node("finalise", finalise)

    builder.set_entry_point("segment")
    builder.add_conditional_edges(
        "segment", after_segment, {"check_segments": "check_segments", "finalise": "finalise"}
    )
    builder.add_conditional_edges(
        "check_segments",
        after_check_segments,
        {"segment": "segment", "use_exchanges": "use_exchanges", "retrieve": "retrieve"},
    )
    builder.add_edge("use_exchanges", "retrieve")
    builder.add_edge("retrieve", "evaluate")
    builder.add_conditional_edges(
        "evaluate",
        after_evaluate,
        {"check_evaluations": "check_evaluations", "map": "map", "finalise": "finalise"},
    )
    builder.add_conditional_edges(
        "check_evaluations", after_check_evaluations, {"evaluate": "evaluate", "map": "map"}
    )
    builder.add_edge("map", "finalise")
    builder.add_edge("finalise", END)
    return builder.compile(checkpointer=checkpointer)


def _err(node: str, exc: AppError) -> dict:
    return {"node": node, "code": type(exc).__name__, "message": exc.message, "item_ref": None}
