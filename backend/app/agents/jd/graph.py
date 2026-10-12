# jd agent: profile + retrieved company policy + llm draft, checked and redrafted.
# with a chat request it revises the current description instead of starting over
#
#   plan_queries -> retrieve_policy -> draft -> review -> finalise -> END
#
#   review -> draft      problems found and attempts left (feedback goes into the prompt)
#   review -> finalise   no problems, or out of attempts (leftovers become warnings)
#   draft  -> finalise   llm call failed (keeps the last reviewed draft if there is one),
#                        or the chat request was only a question (nothing to review)

from collections.abc import Callable
from typing import Any

from langgraph.graph import END, StateGraph

from app.agents.jd.draft import Drafter, gemini_drafter
from app.agents.jd.render import collect_citations, render_markdown
from app.agents.jd.review import review_draft
from app.agents.state import JDState
from app.errors import AppError
from app.logging_config import get_logger
from app.rag.policy_store import PolicyChunk
from app.tools.policy_search import search_many

log = get_logger(__name__)

MAX_ATTEMPTS = 3

# topics every jd needs from policy, whatever the role
STANDARD_TOPICS = [
    "company overview and values",
    "compensation, benefits and bonuses",
    "annual leave and parental leave",
    "working hours and remote work",
    "equal opportunity hiring statement",
    "rules for writing job descriptions",
]

Retriever = Callable[[list[str]], list[PolicyChunk]]


def plan_queries(state: JDState) -> dict:
    profile = state["profile"]
    role = " ".join(p for p in (profile["title"], profile.get("department")) if p)
    request = (state.get("request") or "").strip()

    # revising: search for what was asked, the passages already cited come along anyway
    if request and state.get("current_md"):
        return {"queries": [request, f"{role} role and team"]}

    queries = [f"{role} role and team", *STANDARD_TOPICS]
    if request:
        queries.insert(0, request)
    if profile.get("location"):
        queries.append(f"working location {profile['location']}")
    skills = [r["text"] for r in profile["requirements"] if r["category"] == "technical_skill"]
    if skills:
        queries.append("engineering practice and tools: " + ", ".join(skills[:5]))
    return {"queries": queries}


def make_retrieve_node(retriever: Retriever) -> Callable[[JDState], dict]:
    def retrieve_policy(state: JDState) -> dict:
        prior = list(state.get("prior_chunks") or [])
        try:
            hits = retriever(state["queries"])
        except AppError as exc:
            # no new policy is a degraded draft, not a failed run
            return {
                "policy_chunks": prior,
                "errors": [_error("retrieve_policy", "retrieval_failed", exc.message)],
                "warnings": ["Company policy could not be searched, so no new policy was used."],
            }
        chunks = {c["chunk_id"]: c for c in prior}
        for h in hits:
            chunks.setdefault(
                h.chunk_id,
                {
                    "chunk_id": h.chunk_id,
                    "policy_id": h.policy_id,
                    "policy_title": h.policy_title,
                    "text": h.text,
                    "score": h.score,
                },
            )
        warnings = [] if chunks else ["No relevant company policy was found."]
        return {"policy_chunks": list(chunks.values()), "warnings": warnings}

    return retrieve_policy


def make_draft_node(drafter: Drafter) -> Callable[[JDState], dict]:
    def draft(state: JDState) -> dict:
        attempts = state.get("attempts", 0) + 1
        try:
            result = drafter(
                {
                    "profile": state["profile"],
                    "chunks": state["policy_chunks"],
                    "feedback": state.get("feedback", []),
                    "request": state.get("request"),
                    "current_md": state.get("current_md"),
                    "history": state.get("history", []),
                }
            )
        except AppError as exc:
            return {
                "attempts": attempts,
                "llm_failed": True,
                "errors": [_error("draft", "llm_failed", exc.message)],
            }
        update: dict[str, Any] = {
            "attempts": attempts,
            "llm_failed": False,
            "reply": result.get("reply", ""),
        }
        # an answer-only reply on a redraft keeps the draft already being fixed
        if result.get("draft") is not None or attempts == 1:
            update["draft"] = result.get("draft")
        return update

    return draft


def review(state: JDState) -> dict:
    chunk_ids = {c["chunk_id"] for c in state["policy_chunks"]}
    cleaned, problems = review_draft(
        state["draft"],
        state["profile"],
        chunk_ids,
        allow_extra_sections=bool(state.get("request")),
    )
    log.info(
        "jd_review", job_id=state.get("job_id"), attempt=state["attempts"], problems=len(problems)
    )
    return {"draft": cleaned, "feedback": problems}


def finalise(state: JDState) -> dict:
    warnings = list(state.get("warnings", []))
    reply = state.get("reply", "")
    if state.get("draft") is None:
        # either the llm failed before producing anything, or it only answered a question
        return {
            "is_complete": True,
            "changed": False,
            "description_md": "",
            "citations": [],
            "warnings": warnings,
            "reply": reply,
        }
    if state.get("llm_failed"):
        warnings.append("The AI service failed on a retry, so the last checked draft was kept.")
    if state.get("feedback"):
        warnings += [f"Not fixed automatically: {p}" for p in state["feedback"]]
    return {
        "is_complete": True,
        "changed": True,
        "description_md": render_markdown(state["draft"], state["profile"]),
        "citations": collect_citations(state["draft"], state["policy_chunks"]),
        "warnings": warnings,
        "reply": reply,
    }


def route_after_draft(state: JDState) -> str:
    if state.get("llm_failed") or state.get("draft") is None:
        return "finalise"
    return "review"


def route_after_review(state: JDState) -> str:
    if not state.get("feedback"):
        return "finalise"
    return "draft" if state["attempts"] < MAX_ATTEMPTS else "finalise"


def build_jd_graph(
    checkpointer: Any = None,
    drafter: Drafter = gemini_drafter,
    retriever: Retriever = search_many,
):
    builder = StateGraph(JDState)
    builder.add_node("plan_queries", plan_queries)
    builder.add_node("retrieve_policy", make_retrieve_node(retriever))
    builder.add_node("draft", make_draft_node(drafter))
    builder.add_node("review", review)
    builder.add_node("finalise", finalise)

    builder.set_entry_point("plan_queries")
    builder.add_edge("plan_queries", "retrieve_policy")
    builder.add_edge("retrieve_policy", "draft")
    builder.add_conditional_edges(
        "draft", route_after_draft, {"review": "review", "finalise": "finalise"}
    )
    builder.add_conditional_edges(
        "review", route_after_review, {"draft": "draft", "finalise": "finalise"}
    )
    builder.add_edge("finalise", END)
    return builder.compile(checkpointer=checkpointer)


def _error(node: str, code: str, message: str) -> dict:
    return {"node": node, "code": code, "message": message, "item_ref": None}
