from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from app.agents.feedback.graph import build_feedback_graph
from app.errors import ExternalServiceError

GOOD_SCREENING = {
    "sentences": [
        {"text": "Your CV didn't clearly show Kubernetes in production.", "point_ids": ["P1"]}
    ],
    "closing": "Thank you again for your interest.",
}
BAD = {
    "sentences": [{"text": "You scored lower than other candidates.", "point_ids": ["P1"]}],
    "closing": "",
}


def screening_state(**over):
    base = {
        "stage": "screening",
        "outcome": "not_progressing",
        "job": {"title": "Backend Engineer"},
        "notes": [],
        "sources": {
            "screening_status": "screened",
            "cv_chars": 1500,
            "requirements": [{"id": 1, "text": "Kubernetes in production", "is_mandatory": True,
                              "weight": 1,
             "category": "technical_skill"}],
            "results": [{"id": 11, "requirement_id": 1, "status": "unmet", "quote": None,
                         "verified": False, "reasoning": "No orchestration mentioned.",
                         "confidence": 0.8}],
            "dimensions": [{"id": 70, "dimension": "technical_skills",
                            "justification": "Strong Django, no container work."}],
        },
        "errors": [],
    }  # fmt: skip
    base.update(over)
    return base


def fakes(*drafts, judge=None, notes=None):
    calls: dict[str, list] = {"draft": [], "judge": [], "notes": []}

    def drafter(payload):
        calls["draft"].append(payload)
        d = drafts[min(len(calls["draft"]), len(drafts)) - 1]
        if isinstance(d, Exception):
            raise d
        return d, "fake-model"

    def judge_fn(payload):
        calls["judge"].append(payload)
        if isinstance(judge, Exception):
            raise judge
        return judge or []

    def note_screen(items):
        calls["notes"].append(items)
        return (
            notes
            if notes is not None
            else {
                n["id"]: {"note_id": n["id"], "job_related": True, "reason": "about the work"}
                for n in items
            }
        )

    return calls, {"drafter": drafter, "judge": judge_fn, "note_screen": note_screen}


def graph(fns):
    return build_feedback_graph(InMemorySaver(), min_confidence=0.5, min_cv_chars=300, **fns)


def cfg(tid="t"):
    return {"configurable": {"thread_id": tid}}


def test_a_clean_draft_waits_for_a_person():
    calls, fns = fakes(GOOD_SCREENING)
    g = graph(fns)
    out = g.invoke(screening_state(), cfg())
    assert "__interrupt__" in out and g.get_state(cfg()).next == ("human_review",)
    assert out["guardrail"]["passed"] and out["guardrail"]["judge"]["status"] == "passed"
    p = out["points"][0]
    assert p["requirement_id"] == 1
    assert p["evidence_ref"] == {"type": "screening_result", "id": 11}
    assert p["support"] == [{"type": "score_dimension", "id": 70}]
    assert "Your CV didn't clearly show" in out["draft"]["body"]
    # the drafter never sees anything that identifies the candidate
    assert set(calls["draft"][0]) == {
        "stage", "outcome", "job_title", "points", "notes", "request", "feedback"
    }  # fmt: skip


def test_a_failing_draft_gets_two_redrafts_then_the_generic_letter():
    calls, fns = fakes(BAD)
    out = graph(fns).invoke(screening_state(), cfg())
    assert len(calls["draft"]) == 3
    assert any("other candidates" in f for f in calls["draft"][1]["feedback"])
    assert out["fallback_reason"] == "guardrail"
    assert out["guardrail"]["checked"] == "fallback" and out["guardrail"]["passed"]
    assert "detailed feedback" in out["draft"]["body"]


def test_a_redraft_that_fixes_the_problem_is_kept():
    calls, fns = fakes(BAD, GOOD_SCREENING)
    out = graph(fns).invoke(screening_state(), cfg())
    assert len(calls["draft"]) == 2 and not out.get("fallback_reason")
    assert out["guardrail"]["attempts"] == 2


def test_an_unreadable_or_uncertain_cv_gets_the_generic_letter_without_the_ai():
    for sources, reason in [
        ({"cv_chars": 20}, "unreadable"),
        ({"results": [{"id": 11, "requirement_id": 1, "status": "unmet", "quote": None,
                       "verified": False, "reasoning": "", "confidence": 0.2}]},
         "low_confidence"),
    ]:  # fmt: skip
        calls, fns = fakes(GOOD_SCREENING)
        state = screening_state()
        state["sources"] = {**state["sources"], **sources}
        out = graph(fns).invoke(state, cfg())
        assert out["fallback_reason"] == reason
        assert calls["draft"] == [] and calls["judge"] == []
        assert "__interrupt__" in out


def test_the_ai_being_down_falls_back_and_regenerate_tries_again():
    calls, fns = fakes(ExternalServiceError("busy"), GOOD_SCREENING)
    g = graph(fns)
    out = g.invoke(screening_state(), cfg())
    assert out["fallback_reason"] == "ai_unavailable"
    out = g.invoke(Command(resume={"action": "regenerate", "instruction": "Be brief."}), cfg())
    assert out["fallback_reason"] is None and "Kubernetes" in out["draft"]["body"]
    assert calls["draft"][-1]["request"] == "Be brief."


def test_a_judge_that_cannot_run_leaves_a_warning_not_a_block():
    _, fns = fakes(GOOD_SCREENING, judge=ExternalServiceError("busy"))
    out = graph(fns).invoke(screening_state(), cfg())
    assert out["guardrail"]["passed"] and out["guardrail"]["judge"]["status"] == "not_run"
    assert any("couldn't run" in w for w in out["warnings"])


def test_a_judge_finding_sends_the_draft_back():
    issue = {"category": "harsh_tone", "quote": "didn't clearly show",
             "explanation": "Soften it."}  # fmt: skip
    calls, fns = fakes(GOOD_SCREENING, judge=[issue])
    out = graph(fns).invoke(screening_state(), cfg())
    assert len(calls["draft"]) == 3 and out["fallback_reason"] == "guardrail"
    assert any("Soften it." in f for f in calls["draft"][1]["feedback"])


def test_biased_notes_never_reach_the_drafter():
    notes = [
        {"id": "N1", "author": "interviewer", "text": "Seemed too old for our young team."},
        {"id": "N2", "author": "recruiter", "text": "Talked mostly about the weekend."},
        {"id": "N3", "author": "interviewer", "text": "Clear on Django caching."},
    ]
    verdicts = {
        "N2": {"note_id": "N2", "job_related": False, "reason": "personal impression"},
        "N3": {"note_id": "N3", "job_related": True, "reason": "about the work"},
    }
    calls, fns = fakes(GOOD_SCREENING, notes=verdicts)
    out = graph(fns).invoke(screening_state(notes=notes), cfg())
    # the rule catches the first before the ai is asked
    assert [n["id"] for n in calls["notes"][0]] == ["N2", "N3"]
    assert [n["id"] for n in out["excluded_notes"]] == ["N1", "N2"]
    assert [n["text"] for n in calls["draft"][0]["notes"]] == ["Clear on Django caching."]


def test_notes_are_left_out_when_they_cannot_be_checked():
    calls, fns = fakes(GOOD_SCREENING)
    fns["note_screen"] = lambda items: (_ for _ in ()).throw(ExternalServiceError("busy"))
    notes = [{"id": "N1", "author": "recruiter", "text": "Clear on Django caching."}]
    out = graph(fns).invoke(screening_state(notes=notes), cfg())
    assert out["kept_notes"] == [] and calls["draft"][0]["notes"] == []


def test_edits_are_checked_again_and_approval_waits_for_a_clean_letter():
    _, fns = fakes(GOOD_SCREENING)
    g = graph(fns)
    g.invoke(screening_state(), cfg())
    out = g.invoke(
        Command(resume={"action": "edit", "subject": "Your application",
                        "body": "Dear {candidate_name}, you ranked 40th. "
                                "Salary was not the issue."}),
        cfg(),
    )  # fmt: skip
    assert out["guardrail"]["checked"] == "edit" and not out["guardrail"]["passed"]
    failed = {r["rule"] for r in out["guardrail"]["rules"] if not r["passed"]}
    assert {"scores", "offer"} <= failed
    # approving a letter that fails is refused; it stays in review
    g.invoke(Command(resume={"action": "approve"}), cfg())
    assert g.get_state(cfg()).next == ("human_review",)
    out = g.invoke(
        Command(resume={"action": "edit", "subject": "Your application",
                        "body": "Dear {candidate_name},\n\nThank you for applying."
                                "\n\n{signature}"}),
        cfg(),
    )  # fmt: skip
    assert out["guardrail"]["passed"]
    out = g.invoke(Command(resume={"action": "approve", "user_id": 3}), cfg())
    assert out["status"] == "approved" and g.get_state(cfg()).next == ()
    assert out["draft"]["edited"] is True


def test_discard_ends_the_run():
    _, fns = fakes(GOOD_SCREENING)
    g = graph(fns)
    g.invoke(screening_state(), cfg())
    out = g.invoke(Command(resume={"action": "discard"}), cfg())
    assert out["status"] == "discarded" and g.get_state(cfg()).next == ()


INTERVIEW_SOURCES = {
    "requirements": [
        {"id": 1, "text": "Django", "is_mandatory": True, "weight": 2,
             "category": "technical_skill"},
        {"id": 2, "text": "Kubernetes", "is_mandatory": False, "weight": 1,
             "category": "technical_skill"},
    ],
    "results": [],
    "pairs": [
        {"id": 7, "question": "Django?", "justification": "Concrete example.",
         "links": [{"requirement_id": 1, "coverage": "covered"}]},
        {"id": 8, "question": "Kubernetes?", "justification": "Only the basics.",
         "links": [{"requirement_id": 2, "coverage": "partially_covered"}]},
    ],
    "findings": [],
}  # fmt: skip


def test_interview_feedback_follows_the_decided_outcome():
    draft = {
        "sentences": [
            {
                "text": "You explained your Django work with a concrete example.",
                "point_ids": ["P1"],
            },
            {"text": "A later conversation could go deeper into Kubernetes.", "point_ids": ["P2"]},
        ],
        "closing": "We look forward to speaking with you again.",
    }
    calls, fns = fakes(draft)
    state = {"stage": "interview", "outcome": "next_round", "job": {"title": "Backend Engineer"},
             "notes": [], "sources": INTERVIEW_SOURCES, "errors": []}  # fmt: skip
    out = graph(fns).invoke(state, cfg())
    assert out["guardrail"]["passed"], out["guardrail"]
    assert [p["kind"] for p in out["points"]] == ["strength", "explore_next"]
    assert "invite you to the next stage" in out["draft"]["body"]
    assert calls["judge"][0]["outcome"] == "next_round"


def test_a_rejection_without_any_gap_in_evidence_is_generic():
    sources = {**INTERVIEW_SOURCES, "pairs": INTERVIEW_SOURCES["pairs"][:1]}
    sources["requirements"] = sources["requirements"][:1]
    calls, fns = fakes(GOOD_SCREENING)
    state = {"stage": "interview", "outcome": "not_progressing",
             "job": {"title": "Backend Engineer"}, "notes": [], "sources": sources,
             "errors": []}  # fmt: skip
    out = graph(fns).invoke(state, cfg())
    assert out["fallback_reason"] == "no_evidence" and calls["draft"] == []
