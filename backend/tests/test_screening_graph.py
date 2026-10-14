from pathlib import Path

import pytest

from app.agents.screening.checks import (
    fulfilment,
    mandatory_gaps,
    quote_in_text,
    redact,
    settle,
    verify,
)
from app.agents.screening.graph import build_screening_graph
from app.errors import ExternalServiceError
from tests import cv_fixtures as fx

REQS = [
    {"id": 1, "text": "Python web development", "category": "technical_skill",
     "is_mandatory": True, "weight": 2.0},
    {"id": 2, "text": "Relational databases such as PostgreSQL", "category": "technical_skill",
     "is_mandatory": False, "weight": 1.0},
    {"id": 3, "text": "Kubernetes in production", "category": "technical_skill",
     "is_mandatory": False, "weight": 1.0},
]  # fmt: skip

IDENTITY = {"full_name": "Ada Lovelace", "email": "ada@example.com", "phone": "+44 7700 900123",
            "location": "London"}  # fmt: skip


def extractor(text):
    return {
        **IDENTITY,
        "headline": "Backend Engineer",
        "summary": "Python backend engineer.",
        "total_years_experience": 5,
        "skills": ["Python", "Django"],
        "education": [],
        "work_history": [],
        "certifications": [],
        "projects": [],
    }, "fake-model"


def good_matches(job, requirements, cv, feedback):
    return {
        "matches": [
            {"requirement_id": 1, "status": "met", "confidence": 0.9, "reasoning": "Django.",
             "evidence_quote": "Built REST services in Django for the online shop."},
            {"requirement_id": 2, "status": "met", "confidence": 0.8, "reasoning": "Schemas.",
             "evidence_quote": "Designed PostgreSQL schemas"},
            {"requirement_id": 3, "status": "unmet", "confidence": 0.7, "reasoning": "None.",
             "evidence_quote": ""},
        ],
        "summary": "Strong Python backend fit.",
    }, "fake-model"  # fmt: skip


def run(tmp_path: Path, data: bytes, matcher=good_matches, extract=extractor, name="cv.pdf"):
    path = fx.write(tmp_path, name, data)
    graph = build_screening_graph(extractor=extract, matcher=matcher)
    return graph.invoke(
        {
            "thread_id": "t",
            "cv_document_id": 1,
            "file_path": str(path),
            "job": {"title": "Backend Engineer", "min_years_experience": 3},
            "requirements": REQS,
            "errors": [],
        },
        config={"configurable": {"thread_id": "t"}},
    )


def test_a_good_cv_is_screened_with_verified_evidence(tmp_path):
    seen = {}

    def matcher(job, requirements, cv, feedback):
        seen["text"] = cv["text"]
        return good_matches(job, requirements, cv, feedback)

    final = run(tmp_path, fx.pdf(), matcher=matcher)
    assert final["outcome"] == "screened"
    assert final["fulfilment"] == 75.0  # (2*1 + 1*1 + 0) / 4
    assert all(m["evidence_verified"] for m in final["matches"] if m["status"] == "met")
    assert final["identity"]["email"] == "ada@example.com"
    assert final["model_name"] == "fake-model"
    # the matcher never sees who the person is
    assert "Ada" not in seen["text"] and "ada@example.com" not in seen["text"]
    assert "[name]" in seen["text"] and "[email]" in seen["text"]
    # nothing protected or identifying goes into the stored profile
    assert "email" not in final["profile"] and "full_name" not in final["profile"]


def test_an_invented_quote_is_sent_back_and_fixed(tmp_path):
    feedback_seen = []

    def matcher(job, requirements, cv, feedback):
        feedback_seen.append(list(feedback))
        result, model = good_matches(job, requirements, cv, feedback)
        if not feedback:
            result["matches"][1]["evidence_quote"] = "Ten years of Oracle administration"
        return result, model

    final = run(tmp_path, fx.pdf(), matcher=matcher)
    assert len(feedback_seen) == 2
    assert any("Requirement 2" in p for p in feedback_seen[1])
    assert final["outcome"] == "screened"
    assert final["warnings"] == []


def test_evidence_that_never_checks_out_counts_as_unmet(tmp_path):
    def matcher(job, requirements, cv, feedback):
        result, model = good_matches(job, requirements, cv, feedback)
        result["matches"][1]["evidence_quote"] = "Ten years of Oracle administration"
        return result, model

    final = run(tmp_path, fx.pdf(), matcher=matcher)
    second = next(m for m in final["matches"] if m["requirement_id"] == 2)
    assert second["status"] == "unmet"
    assert "could not be found" in second["reasoning"]
    assert final["fulfilment"] == 50.0
    assert final["warnings"]


def test_an_unmet_must_have_excludes_the_candidate(tmp_path):
    def matcher(job, requirements, cv, feedback):
        result, model = good_matches(job, requirements, cv, feedback)
        result["matches"][0].update(status="unmet", evidence_quote="")
        return result, model

    final = run(tmp_path, fx.pdf(), matcher=matcher)
    assert final["outcome"] == "excluded"
    assert "Python web development" in final["reason"]


def test_a_partly_met_must_have_does_not_exclude(tmp_path):
    def matcher(job, requirements, cv, feedback):
        result, model = good_matches(job, requirements, cv, feedback)
        result["matches"][0]["status"] = "partially_met"
        return result, model

    final = run(tmp_path, fx.pdf(), matcher=matcher)
    assert final["outcome"] == "screened"
    assert final["fulfilment"] == 50.0  # (2*0.5 + 1) / 4


def test_unreadable_and_foreign_cvs_never_reach_the_llm(tmp_path):
    def boom(*args):
        raise AssertionError("the llm must not be called")

    broken = run(tmp_path, fx.pdf()[:300], matcher=boom, extract=boom)
    assert broken["outcome"] == "unreadable"
    assert "damaged" in broken["reason"]

    spanish = run(tmp_path, fx.spanish_pdf(), matcher=boom, extract=boom)
    assert spanish["outcome"] == "not_english"
    assert "English" in spanish["reason"]


def test_an_ai_failure_while_reading_marks_the_cv_failed(tmp_path):
    def failing(text):
        raise ExternalServiceError("The AI service is busy or unavailable right now.")

    final = run(tmp_path, fx.pdf(), extract=failing)
    assert final["outcome"] == "failed"
    assert "busy" in final["reason"]


def test_an_ai_failure_on_the_retry_keeps_the_checked_answer(tmp_path):
    calls = []

    def matcher(job, requirements, cv, feedback):
        calls.append(1)
        if feedback:
            raise ExternalServiceError("The AI service is busy or unavailable right now.")
        result, model = good_matches(job, requirements, cv, feedback)
        result["matches"][1]["evidence_quote"] = "made up"
        return result, model

    final = run(tmp_path, fx.pdf(), matcher=matcher)
    assert len(calls) == 2
    assert final["outcome"] == "screened"
    assert any("failed on a retry" in w for w in final["warnings"])


# --- the checks on their own -----------------------------------------------------


def test_redaction_removes_name_email_and_phone():
    text = "Ada Lovelace\nada@example.com · +44 7700 900123\nLovelace built Django apps."
    out = redact(text, IDENTITY)
    assert "Ada" not in out and "Lovelace" not in out
    assert "ada@example.com" not in out and "7700" not in out
    assert "built Django apps" in out


def test_quote_matching_ignores_case_spacing_and_bullets():
    text = "• Built   REST services\nin Django for the shop."
    assert quote_in_text("built rest services in django", text)
    assert quote_in_text("Built REST services ... in Django", text)
    assert not quote_in_text("built GraphQL services", text)
    assert not quote_in_text("in", text)  # too short to count as evidence


def test_items_picked_from_a_skills_list_are_checked_one_by_one():
    text = "Skills: Python, Django, Ruby on Rails, PostgreSQL, Kafka, Docker, AWS, CI/CD"
    assert quote_in_text("Docker, CI/CD", text)
    assert quote_in_text("AWS", text)
    assert not quote_in_text("Docker, Kubernetes", text)
    # whole words only
    assert not quote_in_text("Go", "Experience with Google Cloud")


def test_verify_flags_unknown_duplicate_and_missing_answers():
    matches = [
        {"requirement_id": 1, "status": "unmet", "evidence_quote": "", "confidence": 2},
        {"requirement_id": 1, "status": "unmet", "evidence_quote": "", "confidence": 0.5},
        {"requirement_id": 9, "status": "met", "evidence_quote": "x", "confidence": 0.5},
    ]
    checked, problems = verify(matches, REQS, "text")
    joined = " ".join(problems)
    assert "id 9" in joined and "twice" in joined and "Requirement 2" in joined
    assert checked[0]["confidence"] == 1.0


def test_settle_fills_missing_answers_as_unmet():
    settled = settle([], REQS)
    assert [m["status"] for m in settled] == ["unmet"] * 3


def test_weighted_fulfilment_and_must_have_gaps():
    matches = [
        {"requirement_id": 1, "status": "partially_met"},
        {"requirement_id": 2, "status": "met"},
        {"requirement_id": 3, "status": "unmet"},
    ]
    assert fulfilment(matches, REQS) == 50.0
    assert mandatory_gaps(matches, REQS) == []
    matches[0]["status"] = "unmet"
    assert mandatory_gaps(matches, REQS) == ["Python web development"]


@pytest.mark.parametrize("status", ["met", "partially_met"])
def test_positive_answers_need_a_quote(status):
    checked, problems = verify(
        [{"requirement_id": 1, "status": status, "evidence_quote": "", "confidence": 0.5}],
        REQS[:1],
        "some cv text",
    )
    assert checked[0]["evidence_verified"] is False
    assert problems


def test_a_quote_ending_at_a_full_stop_still_matches():
    # regression: two candidates were wrongly excluded because "applications." at the end of
    # a sentence did not match the quoted word "applications"
    text = "Summary\nSelf-taught engineer with six years of shipping Python web applications.\nExp"
    assert quote_in_text("six years of shipping Python web applications", text)
    assert quote_in_text("six years of shipping Python web applications.", text)
    assert quote_in_text("Node.js", "Built APIs in Node.js and Go.")
    assert not quote_in_text("Node", "Built APIs in Node.js and Go.")
