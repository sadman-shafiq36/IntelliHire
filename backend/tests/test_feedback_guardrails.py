import pytest

from app.agents.feedback import checks

TITLE = "Backend Engineer"


def letter(middle: str, outcome: str = "not_progressing", stage: str = "interview") -> dict:
    return checks.compose(stage, outcome, TITLE, [{"text": middle, "point_ids": ["P1"]}], "")


def failing(middle: str, outcome: str = "not_progressing") -> set[str]:
    d = letter(middle, outcome)
    return set(checks.check_text(d["subject"], d["body"], outcome))


@pytest.mark.parametrize(
    ("middle", "rule"),
    [
        ("You brought great energy for someone so young.", "protected"),
        ("Your accent made some answers hard to follow.", "protected"),
        ("We felt you weren't a culture fit for the team.", "protected"),
        ("As a recent mother, she may prefer fewer hours.", "protected"),
        ("Your religion was not a factor.", "protected"),
        ("You scored 62% on system design.", "scores"),
        ("You ranked below the shortlist cut-off.", "scores"),
        ("Other candidates showed deeper Kubernetes experience.", "others"),
        ("We had a large number of applications.", "others"),
        ("At 14:05 you described the payments API.", "timestamps"),
        ("On Tuesday you mentioned Django.", "timestamps"),
        ("Contact jane.doe@example.com for details.", "personal_data"),
        ("Call +44 20 7946 0958 to discuss.", "personal_data"),
        ("Dear {first_name}, thanks.", "personal_data"),
        ("We will keep your CV on file for future openings.", "promises"),
        ("We guarantee a reply.", "promises"),
        ("The salary for the role was fixed.", "offer"),
        ("We hope to discuss your start date soon.", "offer"),
        ("We'd like to invite you to the next stage of the process.", "outcome"),
    ],
)
def test_each_rule_blocks_what_it_should(middle, rule):
    assert rule in failing(middle)


@pytest.mark.parametrize(
    "middle",
    [
        "You explained how you built the payments API in Django clearly.",
        "You could strengthen how you describe handling a race condition in queue consumers.",
        "Your answer on foreign key constraints and CORS origin checks was well reasoned.",
        "Showing more about running Kubernetes in production would help.",
        "The role asks for 3+ years of Python, which you described well.",
    ],
)
def test_ordinary_feedback_passes(middle):
    assert failing(middle) == set()


@pytest.mark.parametrize("outcome", ["not_progressing", "on_hold", "next_round"])
def test_code_owned_sentences_and_fallbacks_pass_every_rule(outcome):
    d = checks.fallback_draft("interview", outcome, TITLE)
    assert checks.check_text(d["subject"], d["body"], outcome) == {}
    s = checks.fallback_draft("screening", "not_progressing", TITLE)
    assert checks.check_text(s["subject"], s["body"], "not_progressing") == {}
    assert "{candidate_name}" in d["body"] and "{signature}" in d["body"]


def test_outcome_wording_must_agree_with_the_decision():
    assert "outcome" in failing("Unfortunately you were unsuccessful.", "next_round")
    assert "outcome" in failing("We've decided not to take this further.", "on_hold")
    assert "outcome" not in failing("We'd like to hear more about testing.", "next_round")


POINTS = [
    {"id": "P1", "kind": "strength", "requirement": "Django"},
    {"id": "P2", "kind": "development", "requirement": "Kubernetes"},
]


def test_every_sentence_must_rest_on_a_selected_point():
    draft = {
        "sentences": [
            {"text": "Your Django work was clear.", "point_ids": ["P1"]},
            {"text": "You led a large team.", "point_ids": []},
            {"text": "Your Rust is strong.", "point_ids": ["P9"]},
        ],
        "closing": "",
    }
    hits = checks.check_sentences(draft, POINTS, "interview")
    assert hits["unsupported"] == ["You led a large team.", "Your Rust is strong."]


def test_screening_gaps_are_framed_around_the_cv_and_kept_short():
    vague = {"sentences": [{"text": "Kubernetes is missing.", "point_ids": ["P2"]}], "closing": ""}
    assert "unsupported" in checks.check_sentences(vague, POINTS, "screening")
    framed = {
        "sentences": [
            {"text": "Your CV didn't clearly show Kubernetes in production.", "point_ids": ["P2"]}
        ],
        "closing": "",
    }
    assert checks.check_sentences(framed, POINTS, "screening") == {}
    long = {"sentences": [{"text": "Your CV " + "word " * 120, "point_ids": ["P2"]}],
            "closing": ""}  # fmt: skip
    assert "length" in checks.check_sentences(long, POINTS, "screening")


def test_a_judge_finding_counts_only_when_its_words_are_in_the_letter():
    text = "Dear {candidate_name}, you seemed nervous throughout."
    issues = [
        {"category": "harsh_tone", "quote": "you seemed nervous", "explanation": "Character."},
        {"category": "promise", "quote": "we will hire you later", "explanation": "Invented."},
        {"category": "made_up", "quote": "you seemed nervous", "explanation": "Unknown kind."},
    ]
    kept = checks.keep_found(issues, text)
    assert [i["category"] for i in kept] == ["harsh_tone"]
    report = checks.report({}, {"status": "failed", "issues": kept}, "draft", 1)
    assert report["passed"] is False
    assert next(r for r in report["rules"] if r["rule"] == "unsupported")["passed"] is False


def test_biased_notes_are_caught_but_pronouns_alone_are_not():
    assert checks.note_problems("Seemed too old to keep up with a young team")
    assert checks.note_problems("Strong accent, might not fit in with the client")
    assert checks.note_problems("Has kids, may want part-time")
    assert checks.note_problems("He gave a clear answer on Django caching") == []


def test_screening_falls_back_when_the_cv_or_its_evidence_is_uncertain():
    reqs = [{"id": 1, "text": "Python", "is_mandatory": True, "weight": 1}]
    result = {"id": 5, "requirement_id": 1, "status": "unmet", "verified": False, "quote": None,
              "reasoning": "x", "confidence": 0.9}  # fmt: skip
    good = {"screening_status": "screened", "cv_chars": 1000, "requirements": reqs,
            "results": [result]}  # fmt: skip
    assert checks.screening_evidence(good, 0.5, 300)[2] is None
    assert checks.screening_evidence({**good, "cv_chars": 40}, 0.5, 300)[2] == "unreadable"
    assert checks.screening_evidence({**good, "screening_status": None}, 0.5, 300)[2] == (
        "unreadable"
    )
    shaky = {**good, "results": [{**result, "confidence": 0.3}]}
    assert checks.screening_evidence(shaky, 0.5, 300)[2] == "low_confidence"
    assert checks.screening_evidence({**good, "results": []}, 0.5, 300)[2] == "no_evidence"


def test_points_rest_on_real_requirements_and_stored_rows():
    sources = {
        "requirements": [
            {"id": 1, "text": "Django", "is_mandatory": True, "weight": 2,
             "category": "technical_skill"},
            {"id": 2, "text": "Kubernetes", "is_mandatory": True, "weight": 1,
             "category": "technical_skill"},
            {"id": 3, "text": "Mentoring", "is_mandatory": False, "weight": 1, "category": "other"},
        ],
        "results": [{"id": 31, "requirement_id": 3, "status": "met", "verified": True,
                     "quote": "mentored two juniors", "reasoning": "", "confidence": 0.9}],
        "pairs": [
            {"id": 7, "question": "Django?", "justification": "Concrete [T4].",
             "links": [{"requirement_id": 1, "coverage": "covered"}]},
            {"id": 8, "question": "Kubernetes?", "justification": "Vague on operators.",
             "links": [{"requirement_id": 2, "coverage": "partially_covered"}]},
        ],
        "findings": [{"id": 50, "kind": "weakness", "text": "Thin on Kubernetes.",
                      "requirement_ids": [2, 99]}],
    }  # fmt: skip
    requirements, findings, reason = checks.interview_evidence(sources)
    assert reason is None and findings[0]["requirement_ids"] == [2]
    points = checks.select_points("interview", "not_progressing", requirements, findings, [])
    assert [(p["kind"], p["requirement_id"], p["evidence_ref"]) for p in points] == [
        ("strength", 1, {"type": "qa_pair", "id": 7}),
        ("development", 2, {"type": "qa_pair", "id": 8}),
    ]
    # transcript markers never reach the drafter
    assert points[0]["evidence"] == "Concrete."
    assert {"type": "assessment_finding", "id": 50} in points[1]["support"]
    # the next round may also name what was never discussed
    nxt = checks.select_points("interview", "next_round", requirements, findings, [])
    assert [p["requirement_id"] for p in nxt if p["kind"] == "explore_next"] == [2, 3]
