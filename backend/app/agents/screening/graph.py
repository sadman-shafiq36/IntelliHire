# screening one cv against one job
#
#   read_cv -> extract -> match -> verify -> decide -> END
#
#   read_cv -> decide    unreadable file or not english (nothing for the llm to do)
#   extract -> decide    the llm failed
#   verify  -> match     quotes not found in the cv and attempts left (feedback in the prompt)
#   match   -> decide    the llm failed on a retry (keeps the last checked answer)

from pathlib import Path
from typing import Any

from langgraph.graph import END, StateGraph

from app.agents.screening.checks import (
    fulfilment,
    mandatory_gaps,
    redact,
    settle,
    verify,
)
from app.agents.screening.llm import Extractor, Matcher, llm_extractor, llm_matcher
from app.agents.state import ScreeningState
from app.errors import AppError
from app.services.cv_text import read_cv as read_cv_file
from app.services.documents import DocumentReadError

MAX_MATCH_ATTEMPTS = 2

IDENTITY = ("full_name", "email", "phone", "location")


def read_cv(state: ScreeningState) -> dict:
    try:
        cv = read_cv_file(Path(state["file_path"]))
    except DocumentReadError as exc:
        return {"outcome": "unreadable", "reason": str(exc)}
    if cv.language != "en":
        return {
            "outcome": "not_english",
            "reason": "This CV is not in English. Screening supports English CVs only.",
            "page_count": cv.page_count,
            "used_ocr": cv.used_ocr,
            "language": cv.language,
        }
    return {
        "raw_text": cv.text,
        "page_count": cv.page_count,
        "used_ocr": cv.used_ocr,
        "language": cv.language,
    }


def make_extract(extractor: Extractor):
    def extract(state: ScreeningState) -> dict:
        try:
            attrs, model = extractor(state["raw_text"])
        except AppError as exc:
            return {"outcome": "failed", "reason": exc.message, "errors": [_err("extract", exc)]}
        identity = {k: (attrs.get(k) or "").strip() for k in IDENTITY}
        profile = {k: v for k, v in attrs.items() if k not in IDENTITY}
        return {
            "identity": identity,
            "profile": profile,
            # the matcher and the stored text never carry the person's details
            "redacted_text": redact(state["raw_text"], identity),
            "model_name": model,
        }

    return extract


def make_match(matcher: Matcher):
    def match(state: ScreeningState) -> dict:
        attempts = state.get("attempts", 0) + 1
        cv = {
            "text": state["redacted_text"],
            "years": state["profile"].get("total_years_experience") or 0,
        }
        try:
            result, model = matcher(
                state["job"], state["requirements"], cv, state.get("feedback", [])
            )
        except AppError as exc:
            update: dict[str, Any] = {
                "attempts": attempts,
                "match_failed": True,
                "errors": [_err("match", exc)],
            }
            if not state.get("matches"):
                update.update(outcome="failed", reason=exc.message)
            return update
        return {
            "attempts": attempts,
            "match_failed": False,
            "candidate_matches": result.get("matches", []),
            "summary": result.get("summary", ""),
            "model_name": model,
        }

    return match


def check_evidence(state: ScreeningState) -> dict:
    matches, problems = verify(
        state["candidate_matches"], state["requirements"], state["redacted_text"]
    )
    return {"matches": matches, "feedback": problems}


def decide(state: ScreeningState) -> dict:
    if state.get("outcome") in ("unreadable", "not_english"):
        return {}
    if state.get("outcome") == "failed" and not state.get("matches"):
        return {}
    requirements = state["requirements"]
    matches = settle(state.get("matches", []), requirements)
    warnings = []
    unverified = [
        m
        for m in state.get("matches", [])
        if m["status"] != "unmet" and not m.get("evidence_verified")
    ]
    if state.get("match_failed"):
        warnings.append("The AI service failed on a retry, so the last checked answer was used.")
    if unverified:
        warnings.append(
            f"{len(unverified)} judgement(s) had no quote from the CV and were counted as unmet."
        )
    gaps = mandatory_gaps(matches, requirements)
    return {
        "outcome": "excluded" if gaps else "screened",
        "reason": ("Does not meet the must-have: " + "; ".join(gaps)) if gaps else None,
        "matches": matches,
        "fulfilment": fulfilment(matches, requirements),
        "warnings": warnings,
    }


def after_read(state: ScreeningState) -> str:
    return "decide" if state.get("outcome") else "extract"


def after_extract(state: ScreeningState) -> str:
    return "decide" if state.get("outcome") == "failed" else "match"


def after_match(state: ScreeningState) -> str:
    # a failed retry goes straight on with the matches from the previous, checked attempt
    return "decide" if state.get("match_failed") else "verify"


def after_verify(state: ScreeningState) -> str:
    if state.get("feedback") and state["attempts"] < MAX_MATCH_ATTEMPTS:
        return "match"
    return "decide"


def build_screening_graph(
    checkpointer: Any = None,
    extractor: Extractor = llm_extractor,
    matcher: Matcher = llm_matcher,
):
    builder = StateGraph(ScreeningState)
    builder.add_node("read_cv", read_cv)
    builder.add_node("extract", make_extract(extractor))
    builder.add_node("match", make_match(matcher))
    builder.add_node("verify", check_evidence)
    builder.add_node("decide", decide)

    builder.set_entry_point("read_cv")
    builder.add_conditional_edges("read_cv", after_read, {"extract": "extract", "decide": "decide"})
    builder.add_conditional_edges("extract", after_extract, {"match": "match", "decide": "decide"})
    builder.add_conditional_edges("match", after_match, {"verify": "verify", "decide": "decide"})
    builder.add_conditional_edges("verify", after_verify, {"match": "match", "decide": "decide"})
    builder.add_edge("decide", END)
    return builder.compile(checkpointer=checkpointer)


def _err(node: str, exc: AppError) -> dict:
    return {"node": node, "code": type(exc).__name__, "message": exc.message, "item_ref": None}
