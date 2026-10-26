# the candidate feedback composer: one letter to one candidate, at screening or after the
# interview. code picks what the letter may say, the model words it, rules and a judge check
# it, and a person approves, edits, regenerates or discards it. nothing here sends anything
#
#   load_evidence -> screen_inputs -> select_points -> draft_feedback -> guardrail_check
#       -> human_review (interrupt) -> finalize -> END
#
#   screen_inputs   -> fallback          the cv couldn't be read or the evidence is too uncertain
#   select_points   -> fallback          nothing to say that rests on evidence
#   draft_feedback  -> fallback          the ai is unavailable
#   guardrail_check -> draft_feedback    a draft broke a rule, up to two redrafts
#   guardrail_check -> fallback          still breaking a rule after two redrafts
#   fallback        -> guardrail_check   the generic letter is checked too
#   human_review    -> guardrail_check   an edit is checked again before it can be approved
#   human_review    -> draft_feedback    regenerate
#   human_review    -> human_review      approve while the checks fail is refused

from typing import Any

from langgraph.graph import END, StateGraph
from langgraph.types import interrupt

from app.agents.feedback import checks
from app.agents.feedback.llm import (
    Drafter,
    Judge,
    NoteScreen,
    llm_drafter,
    llm_judge,
    llm_note_screen,
)
from app.agents.state import FeedbackState
from app.config import settings
from app.errors import AppError

MAX_DRAFTS = 3


def make_load_evidence(min_confidence: float, min_cv_chars: int):
    def load_evidence(state: FeedbackState) -> dict:
        sources = state.get("sources") or {}
        if state["stage"] == "screening":
            requirements, dimensions, reason = checks.screening_evidence(
                sources, min_confidence, min_cv_chars
            )
            return {
                "requirements": requirements,
                "dimensions": dimensions,
                "findings": [],
                "fallback_reason": reason,
            }
        requirements, findings, reason = checks.interview_evidence(sources)
        return {
            "requirements": requirements,
            "findings": findings,
            "dimensions": [],
            "fallback_reason": reason,
        }

    return load_evidence


def make_screen_inputs(note_screen: NoteScreen):
    def screen_inputs(state: FeedbackState) -> dict:
        kept, excluded, unchecked = [], [], []
        for note in state.get("notes") or []:
            found = checks.note_problems(note["text"])
            if found:
                excluded.append({**note, "reason": "Mentions " + ", ".join(found)})
            else:
                unchecked.append(note)
        warnings = []
        if unchecked:
            try:
                verdicts = note_screen(unchecked)
            except AppError:
                verdicts = {}
                warnings.append("The notes couldn't be checked by the AI, so none were used.")
            for note in unchecked:
                v = verdicts.get(note["id"])
                if v and v["job_related"]:
                    kept.append(note)
                else:
                    reason = v["reason"] if v else "couldn't be checked"
                    excluded.append({**note, "reason": f"Not job-related: {reason}"})
        return {"kept_notes": kept, "excluded_notes": excluded, "warnings": warnings}

    return screen_inputs


def select_points(state: FeedbackState) -> dict:
    points = checks.select_points(
        state["stage"],
        state["outcome"],
        state.get("requirements") or [],
        state.get("findings") or [],
        state.get("dimensions") or [],
    )
    gaps = [p for p in points if p["kind"] != "strength"]
    # a no without a reason that rests on evidence gets the generic letter
    if not points or (state["outcome"] == "not_progressing" and not gaps):
        return {"points": points, "fallback_reason": "no_evidence"}
    return {"points": points}


def make_draft_feedback(drafter: Drafter):
    def draft_feedback(state: FeedbackState) -> dict:
        attempts = state.get("attempts", 0) + 1
        payload = {
            "stage": state["stage"],
            "outcome": state["outcome"],
            "job_title": state["job"]["title"],
            "points": state["points"],
            "notes": state.get("kept_notes") or [],
            "request": state.get("request"),
            "feedback": state.get("feedback") or [],
        }
        try:
            answer, model = drafter(payload)
        except AppError as exc:
            return {"attempts": attempts, "draft_failed": True, "errors": [_err("draft", exc)]}
        sentences = [
            {"text": " ".join((s.get("text") or "").split()), "point_ids": s.get("point_ids") or []}
            for s in answer.get("sentences") or []
            if (s.get("text") or "").strip()
        ]
        draft = checks.compose(
            state["stage"],
            state["outcome"],
            state["job"]["title"],
            sentences,
            answer.get("closing") or "",
        )
        return {
            "attempts": attempts,
            "draft_failed": False,
            "draft": draft,
            "check_target": "draft",
            "model_name": model,
        }

    return draft_feedback


def make_guardrail_check(judge: Judge):
    def guardrail_check(state: FeedbackState) -> dict:
        target = state.get("check_target", "draft")
        draft = state["draft"]
        hits = checks.check_text(draft["subject"], draft["body"], state["outcome"])
        if target == "draft":
            for rule, found in checks.check_sentences(
                draft, state["points"], state["stage"]
            ).items():
                hits[rule] = hits.get(rule, []) + found
        warnings = []
        if target == "fallback":
            # fixed wording written and tested in code; no need to spend a call on it
            judge_result: dict[str, Any] = {"status": "skipped", "issues": []}
        else:
            text = f"{draft['subject']}\n{draft['body']}"
            try:
                raw = judge(
                    {
                        "outcome": state["outcome"],
                        "points": state.get("points") or [],
                        "subject": draft["subject"],
                        "body": draft["body"],
                    }
                )
                issues = checks.keep_found(raw, text)
                judge_result = {"status": "failed" if issues else "passed", "issues": issues}
            except AppError:
                judge_result = {"status": "not_run", "issues": []}
                warnings.append(
                    "The AI review of this letter couldn't run; only the fixed rules checked it. "
                    "Read it carefully before approving."
                )
        result = checks.report(hits, judge_result, target, state.get("attempts", 0))
        return {
            "guardrail": result,
            "feedback": checks.fixes(hits, judge_result["issues"], state["stage"]),
            "warnings": warnings,
        }

    return guardrail_check


def fallback(state: FeedbackState) -> dict:
    reason = state.get("fallback_reason")
    if reason is None:
        reason = "ai_unavailable" if state.get("draft_failed") else "guardrail"
    return {
        "draft": checks.fallback_draft(state["stage"], state["outcome"], state["job"]["title"]),
        "check_target": "fallback",
        "fallback_reason": reason,
        "model_name": state.get("model_name") or "none",
    }


def human_review(state: FeedbackState) -> dict:
    # the run stops here and is saved in the checkpointer; it carries on when a person
    # approves, edits, regenerates or discards, which may be days later in another process
    decision = interrupt({"guardrail_passed": state["guardrail"]["passed"]})
    action = decision.get("action")
    update: dict[str, Any] = {"review": {"action": action, "user_id": decision.get("user_id")}}
    if action == "edit":
        update["draft"] = {
            "subject": " ".join((decision.get("subject") or "").split()),
            "body": (decision.get("body") or "").strip(),
            "sentences": [],
            "closing": "",
            "edited": True,
        }
        update["check_target"] = "edit"
    elif action == "regenerate":
        update.update(attempts=0, feedback=[], request=decision.get("instruction") or None)
        if state.get("fallback_reason") not in checks.EVIDENCE_REASONS:
            update["fallback_reason"] = None
    return update


def finalize(state: FeedbackState) -> dict:
    action = state["review"]["action"]
    return {"status": "approved" if action == "approve" else "discarded", "is_complete": True}


def after_screen(state: FeedbackState) -> str:
    return "fallback" if state.get("fallback_reason") else "select_points"


def after_select(state: FeedbackState) -> str:
    return "fallback" if state.get("fallback_reason") else "draft_feedback"


def after_draft(state: FeedbackState) -> str:
    return "fallback" if state.get("draft_failed") else "guardrail_check"


def after_guardrail(state: FeedbackState) -> str:
    if state.get("check_target") in ("edit", "fallback") or state["guardrail"]["passed"]:
        return "human_review"
    return "draft_feedback" if state.get("attempts", 0) < MAX_DRAFTS else "fallback"


def after_review(state: FeedbackState) -> str:
    action = state["review"]["action"]
    if action == "approve":
        return "finalize" if state["guardrail"]["passed"] else "human_review"
    if action == "edit":
        return "guardrail_check"
    if action == "regenerate":
        if state.get("fallback_reason") in checks.EVIDENCE_REASONS:
            return "fallback"
        return "draft_feedback"
    return "finalize"


def build_feedback_graph(
    checkpointer: Any = None,
    drafter: Drafter = llm_drafter,
    judge: Judge = llm_judge,
    note_screen: NoteScreen = llm_note_screen,
    min_confidence: float | None = None,
    min_cv_chars: int | None = None,
):
    # human_review interrupts, so a real run needs a checkpointer to resume from
    builder = StateGraph(FeedbackState)
    builder.add_node(
        "load_evidence",
        make_load_evidence(
            settings.feedback_min_confidence if min_confidence is None else min_confidence,
            settings.feedback_min_cv_chars if min_cv_chars is None else min_cv_chars,
        ),
    )
    builder.add_node("screen_inputs", make_screen_inputs(note_screen))
    builder.add_node("select_points", select_points)
    builder.add_node("draft_feedback", make_draft_feedback(drafter))
    builder.add_node("guardrail_check", make_guardrail_check(judge))
    builder.add_node("fallback", fallback)
    builder.add_node("human_review", human_review)
    builder.add_node("finalize", finalize)

    builder.set_entry_point("load_evidence")
    builder.add_edge("load_evidence", "screen_inputs")
    builder.add_conditional_edges(
        "screen_inputs", after_screen, {"fallback": "fallback", "select_points": "select_points"}
    )
    builder.add_conditional_edges(
        "select_points", after_select, {"fallback": "fallback", "draft_feedback": "draft_feedback"}
    )
    builder.add_conditional_edges(
        "draft_feedback",
        after_draft,
        {"fallback": "fallback", "guardrail_check": "guardrail_check"},
    )
    builder.add_conditional_edges(
        "guardrail_check",
        after_guardrail,
        {
            "human_review": "human_review",
            "draft_feedback": "draft_feedback",
            "fallback": "fallback",
        },
    )
    builder.add_edge("fallback", "guardrail_check")
    builder.add_conditional_edges(
        "human_review",
        after_review,
        {
            "finalize": "finalize",
            "human_review": "human_review",
            "guardrail_check": "guardrail_check",
            "draft_feedback": "draft_feedback",
            "fallback": "fallback",
        },
    )
    builder.add_edge("finalize", END)
    return builder.compile(checkpointer=checkpointer)


def _err(node: str, exc: AppError) -> dict:
    return {"node": node, "code": type(exc).__name__, "message": exc.message, "item_ref": None}
