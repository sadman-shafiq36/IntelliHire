import pytest

from app.agents.scoring import checks
from app.agents.scoring.graph import build_scoring_graph
from app.errors import ExternalServiceError

REQS = [
    {"id": 1, "text": "Python web development", "category": "technical_skill",
     "is_mandatory": True, "weight": 2.0},
    {"id": 2, "text": "PostgreSQL", "category": "technical_skill",
     "is_mandatory": False, "weight": 1.0},
    {"id": 3, "text": "Led a team", "category": "responsibility",
     "is_mandatory": False, "weight": 1.0},
]  # fmt: skip

RESULTS = [
    {"requirement_id": 1, "status": "met", "evidence_quote": "Built REST services in Django"},
    {"requirement_id": 2, "status": "partially_met", "evidence_quote": "some SQL"},
    {"requirement_id": 3, "status": "unmet", "evidence_quote": ""},
]

CV = (
    "Backend engineer. Built REST services in Django for an online shop serving 2 million "
    "users. Wrote some SQL reports. Skills: Python, Docker, AWS."
)
KEYWORDS = [
    {"term": "Python", "aliases": []},
    {"term": "PostgreSQL", "aliases": ["Postgres"]},
    {"term": "Docker", "aliases": []},
    {"term": "Kubernetes", "aliases": ["k8s"]},
]


def _state(**overrides):
    state = {
        "thread_id": "t",
        "candidate_id": 1,
        "job": {"title": "Backend Engineer", "min_years_experience": 4},
        "requirements": REQS,
        "results": RESULTS,
        "cv_text": CV,
        "years": 2,
        "keywords": KEYWORDS,
        "description_vector": [1.0, 0.0],
        "errors": [],
    }
    return state | overrides


def embedder(texts):
    # cosine 0.8 against [1, 0]
    return [[0.8, 0.6] for _ in texts]


def assessor_returning(*answers):
    calls = []

    def assessor(payload):
        calls.append(payload)
        return {"dimensions": answers[min(len(calls), len(answers)) - 1]}, "fake-model"

    assessor.calls = calls
    return assessor


def test_evidence_scores_group_requirements_and_count_years():
    base = checks.evidence_scores(RESULTS, REQS, years=2, min_years=4)
    # technical: met (2) + partly (0.5 x 1) out of 3
    assert base["technical_skills"]["score"] == 83
    # experience: led a team unmet + 2 of 4 years -> 0.5 out of 2
    assert base["relevant_experience"]["score"] == 25
    assert "qualifications" not in base
    assert base["technical_skills"]["basis"].startswith("1 of 2 met, 1 partly met")


def test_description_match_combines_key_terms_and_wording():
    match = checks.description_match(KEYWORDS, CV, similarity=0.80)
    assert match["found"] == ["Python", "Docker"]
    assert match["missing"] == ["PostgreSQL", "Kubernetes"]
    assert match["coverage"] == 50 and match["semantic"] == 80
    assert match["score"] == 65
    assert "missing: PostgreSQL, Kubernetes" in match["basis"]


def test_key_terms_match_whole_words_and_aliases():
    found, _ = checks.keyword_coverage(
        [{"term": "Go", "aliases": []}, {"term": "PostgreSQL", "aliases": ["Postgres"]}],
        "Worked at Google with Postgres and CI/CD.",
    )
    assert found == ["PostgreSQL"]


def test_adjustments_are_capped_and_snapped():
    assert checks.snap(7) == 5
    assert checks.snap(-40) == -15
    assert checks.snap("nonsense") == 0


def test_weights_add_up_to_100_over_scoreable_dimensions():
    weights = checks.normalise_weights(
        {"technical_skills": 50, "relevant_experience": 30, "qualifications": 10, "jd_match": 10},
        applicable=["technical_skills", "relevant_experience", "jd_match"],
    )
    assert weights["qualifications"] == 0
    assert sum(weights.values()) == 100
    assert weights == {
        "technical_skills": 56,
        "relevant_experience": 33,
        "qualifications": 0,
        "jd_match": 11,
    }
    assert checks.normalise_weights({}, ["jd_match"])["jd_match"] == 100


def test_aggregate_ignores_dimensions_without_a_score():
    scores = {"technical_skills": 80, "jd_match": 60}
    weights = {"technical_skills": 50, "qualifications": 30, "jd_match": 20}
    assert checks.aggregate(scores, weights) == pytest.approx(74.3)


def test_graph_applies_a_quoted_adjustment():
    assessor = assessor_returning(
        [
            {"dimension": "technical_skills", "adjustment": 6,
             "justification": "Large-scale Django work.",
             "evidence_quotes": ["online shop serving 2 million users"]},
            {"dimension": "relevant_experience", "adjustment": 0,
             "justification": "Two years, no leadership.", "evidence_quotes": []},
        ]
    )  # fmt: skip
    final = build_scoring_graph(assessor=assessor, embedder=embedder).invoke(_state())

    assert final["outcome"] == "scored"
    tech = final["dimensions"]["technical_skills"]
    assert (tech["base_score"], tech["adjustment"], tech["score"]) == (83, 5, 88)
    assert tech["details"]["quotes"] == ["online shop serving 2 million users"]
    assert final["dimensions"]["jd_match"]["score"] == 65
    assert "qualifications" not in final["dimensions"]
    assert len(assessor.calls) == 1
    # identity never reaches the assessor, only the redacted cv text
    assert set(assessor.calls[0]) == {"job", "base", "applicable", "years", "cv_text", "feedback"}


def test_an_invented_quote_is_sent_back_then_dropped():
    bad = [
        {"dimension": "technical_skills", "adjustment": 15, "justification": "Expert.",
         "evidence_quotes": ["ten years of Kubernetes"]},
        {"dimension": "relevant_experience", "adjustment": 0, "justification": "Fine.",
         "evidence_quotes": []},
    ]  # fmt: skip
    assessor = assessor_returning(bad, bad)
    final = build_scoring_graph(assessor=assessor, embedder=embedder).invoke(_state())

    assert len(assessor.calls) == 2
    assert any("ten years of Kubernetes" in f for f in assessor.calls[1]["feedback"])
    tech = final["dimensions"]["technical_skills"]
    assert tech["adjustment"] == 0 and tech["score"] == 83
    # without its quote the ai's text would claim more than the score shows
    assert tech["justification"] == tech["details"]["basis"]
    assert any("not applied" in w for w in final["warnings"])


def test_a_corrected_answer_on_retry_is_used():
    first = [{"dimension": "technical_skills", "adjustment": 10, "justification": "Big.",
              "evidence_quotes": ["made up"]}]  # fmt: skip
    second = [
        {"dimension": "technical_skills", "adjustment": 10, "justification": "Big.",
         "evidence_quotes": ["2 million users"]},
        {"dimension": "relevant_experience", "adjustment": -5, "justification": "Short.",
         "evidence_quotes": ["Wrote some SQL reports"]},
    ]  # fmt: skip
    final = build_scoring_graph(
        assessor=assessor_returning(first, second), embedder=embedder
    ).invoke(_state())
    assert final["dimensions"]["technical_skills"]["score"] == 93
    assert final["dimensions"]["relevant_experience"]["score"] == 20
    assert final["warnings"] == []


def test_an_adjustment_past_100_is_recorded_as_applied():
    full = [{"requirement_id": r["id"], "status": "met", "evidence_quote": "x"} for r in REQS]
    answers = [
        {"dimension": "technical_skills", "adjustment": 10, "justification": "Scale.",
         "evidence_quotes": ["2 million users"]},
        {"dimension": "relevant_experience", "adjustment": 0, "justification": "OK.",
         "evidence_quotes": []},
    ]  # fmt: skip
    final = build_scoring_graph(assessor=assessor_returning(answers), embedder=embedder).invoke(
        _state(results=full)
    )
    tech = final["dimensions"]["technical_skills"]
    assert (tech["base_score"], tech["adjustment"], tech["score"]) == (100, 0, 100)


def test_ai_failure_fails_the_candidate_rather_than_guessing():
    def failing(payload):
        raise ExternalServiceError("The AI service is busy or unavailable right now.")

    final = build_scoring_graph(assessor=failing, embedder=embedder).invoke(_state())
    assert final["outcome"] == "failed"
    assert "busy" in final["reason"]
    assert final["errors"][0]["node"] == "assess"


def test_embedding_failure_fails_before_any_llm_call():
    def failing_embedder(texts):
        raise ExternalServiceError("The AI service could not process the text.")

    assessor = assessor_returning([])
    final = build_scoring_graph(assessor=assessor, embedder=failing_embedder).invoke(_state())
    assert final["outcome"] == "failed"
    assert assessor.calls == []


def test_same_inputs_give_the_same_scores():
    answers = [
        {"dimension": "technical_skills", "adjustment": 4, "justification": "Scale.",
         "evidence_quotes": ["2 million users"]},
        {"dimension": "relevant_experience", "adjustment": 0, "justification": "OK.",
         "evidence_quotes": []},
    ]  # fmt: skip
    runs = [
        build_scoring_graph(assessor=assessor_returning(answers), embedder=embedder).invoke(
            _state()
        )["dimensions"]
        for _ in range(3)
    ]
    assert runs[0] == runs[1] == runs[2]
