from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.agents.scoring.graph import build_scoring_graph
from app.errors import ExternalServiceError
from app.models.analysis import AgentRun
from app.models.job import Job
from tests import cv_fixtures as fx
from tests.conftest import auth_header
from tests.test_screening_api import _screen, _upload, fake_screening, job  # noqa: F401

PROPOSAL = {
    "weights": [
        {"dimension": "technical_skills", "weight": 50, "reason": "A hands-on role."},
        {"dimension": "relevant_experience", "weight": 30, "reason": "Three years asked."},
        {"dimension": "qualifications", "weight": 10, "reason": "None asked."},
        {"dimension": "jd_match", "weight": 10, "reason": "Wording check."},
    ],
    "keywords": [
        {"term": "PostgreSQL", "aliases": ["Postgres"]},
        {"term": "Kubernetes", "aliases": ["k8s"]},
        # not in the job's text, so it must be dropped
        {"term": "Rust", "aliases": []},
        # in the text, but a phrase rather than a key term
        {"term": "Relational databases such as PostgreSQL", "aliases": []},
    ],
}


def proposer(payload):
    return PROPOSAL, "fake-model"


def assessor(payload):
    return {
        "dimensions": [
            {
                "dimension": d,
                "adjustment": 5 if d == "technical_skills" else 0,
                "justification": f"{d} judged for test.",
                "evidence_quotes": ["tuned slow queries"] if d == "technical_skills" else [],
            }
            for d in payload["applicable"]
        ]
    }, "fake-model"


@pytest.fixture
def fake_scoring(monkeypatch: pytest.MonkeyPatch):
    calls = {"proposer": 0}

    def counting_proposer(payload):
        calls["proposer"] += 1
        return proposer(payload)

    @contextmanager
    def graph():
        yield build_scoring_graph(assessor=assessor)

    monkeypatch.setattr("app.worker.tasks._scoring_graph", graph)
    monkeypatch.setattr("app.worker.tasks._weights_proposer", lambda: counting_proposer)
    return calls


@pytest.fixture
def screened(client: TestClient, interviewer_token: str, job, inline_jobs, fake_screening):  # noqa: F811
    _upload(
        client,
        interviewer_token,
        job["id"],
        [
            ("ada.pdf", fx.pdf("Ada Lovelace", "ada@example.com")),
            ("grace.pdf", fx.pdf("Grace Hopper", "grace@example.com")),
            ("linus.pdf", fx.pdf("Linus Frontend", "linus@example.com")),
        ],
    )
    _screen(client, interviewer_token, job["id"], inline_jobs)
    return job


def _score(client, token, job_id, inline_jobs, rescore=False):
    response = client.post(
        f"/api/v1/jobs/{job_id}/scoring", json={"rescore": rescore}, headers=auth_header(token)
    )
    assert response.status_code == 202, response.text
    assert inline_jobs[-1]["error"] is None, inline_jobs[-1]["error"]
    return response.json(), inline_jobs[-1]["result"]


def _results(client, token, job_id):
    return client.get(f"/api/v1/jobs/{job_id}/scoring", headers=auth_header(token)).json()


def test_scoring_ranks_screened_candidates_with_ai_weights(
    client: TestClient, interviewer_token: str, screened, inline_jobs, fake_scoring, db_session
):
    started, result = _score(client, interviewer_token, screened["id"], inline_jobs)
    # the excluded candidate is not scored
    assert started["candidate_count"] == 2
    assert result["scored"] == 2 and result["failed"] == 0

    data = _results(client, interviewer_token, screened["id"])
    profile = data["profile"]
    assert profile["source"] == "ai"
    # qualifications has no requirements, so its weight moves to the others
    assert profile["weights"] == {
        "technical_skills": 56,
        "relevant_experience": 33,
        "qualifications": 0,
        "jd_match": 11,
    }
    assert profile["suggested"]["reasons"]["technical_skills"] == "A hands-on role."
    assert [k["term"] for k in profile["keywords"]] == ["PostgreSQL", "Kubernetes"]

    ranked = data["ranked"]
    assert [r["full_name"] for r in ranked] == ["Ada Lovelace", "Grace Hopper"]
    ada = ranked[0]
    # technical: met 2 + met 1 of weight 4 = 75, +5 quoted = 80. experience: 5 of 3 years = 100
    assert ada["dimensions"]["technical_skills"] == 80
    assert ada["dimensions"]["relevant_experience"] == 100
    assert "qualifications" not in ada["dimensions"]
    assert data["waiting_count"] == 0 and data["excluded_count"] == 1
    runs = db_session.scalars(select(AgentRun).where(AgentRun.graph_name == "candidate_scoring"))
    assert len(runs.all()) == 2


def test_breakdown_explains_each_dimension(
    client: TestClient, interviewer_token: str, screened, inline_jobs, fake_scoring
):
    _score(client, interviewer_token, screened["id"], inline_jobs)
    ada = _results(client, interviewer_token, screened["id"])["ranked"][0]
    body = client.get(
        f"/api/v1/jobs/{screened['id']}/scoring/{ada['candidate_id']}",
        headers=auth_header(interviewer_token),
    ).json()

    dims = {d["dimension"]: d for d in body["dimensions"]}
    tech = dims["technical_skills"]
    assert (tech["base_score"], tech["adjustment"], tech["score"]) == (75, 5, 80)
    assert tech["details"]["quotes"] == ["tuned slow queries"]
    assert tech["justification"] == "technical_skills judged for test."
    assert dims["jd_match"]["details"]["found"] == ["PostgreSQL"]
    assert dims["jd_match"]["details"]["missing"] == ["Kubernetes"]
    # contributions add up to the aggregate
    total = sum(d["contribution"] for d in body["dimensions"])
    assert total == pytest.approx(body["aggregate_score"], abs=0.2)
    assert body["model_name"] == "fake-model" and body["prompt_version"] == "score-v2"
    # no identity besides the name in a score breakdown
    assert set(body["candidate"]) == {"id", "full_name"}


def test_editing_weights_recomputes_without_the_ai(
    client: TestClient, interviewer_token: str, screened, inline_jobs, fake_scoring
):
    _score(client, interviewer_token, screened["id"], inline_jobs)
    before = _results(client, interviewer_token, screened["id"])["ranked"][0]
    runs_before = len(inline_jobs)

    response = client.put(
        f"/api/v1/jobs/{screened['id']}/scoring/weights",
        json={"weights": {"technical_skills": 0, "relevant_experience": 1, "jd_match": 0}},
        headers=auth_header(interviewer_token),
    )
    assert response.status_code == 200, response.text
    assert response.json()["source"] == "edited"
    assert response.json()["weights"]["relevant_experience"] == 100
    assert len(inline_jobs) == runs_before

    after = _results(client, interviewer_token, screened["id"])["ranked"][0]
    assert after["aggregate_score"] == 100 != before["aggregate_score"]
    # dimension scores are stored once and never change with the weights
    assert after["dimensions"] == before["dimensions"]


def test_weights_must_leave_something_to_score(
    client: TestClient, interviewer_token: str, screened
):
    headers = auth_header(interviewer_token)
    url = f"/api/v1/jobs/{screened['id']}/scoring/weights"
    zero = client.put(url, json={"weights": {"qualifications": 50}}, headers=headers)
    assert zero.status_code == 422
    assert "above 0" in zero.json()["error"]["message"]
    bad = client.put(url, json={"weights": {"charm": 50}}, headers=headers)
    assert bad.status_code == 422


def test_edited_weights_survive_the_first_scoring_run(
    client: TestClient, interviewer_token: str, screened, inline_jobs, fake_scoring
):
    client.put(
        f"/api/v1/jobs/{screened['id']}/scoring/weights",
        json={"weights": {"technical_skills": 1, "relevant_experience": 1, "jd_match": 0}},
        headers=auth_header(interviewer_token),
    )
    _score(client, interviewer_token, screened["id"], inline_jobs)
    profile = _results(client, interviewer_token, screened["id"])["profile"]
    assert profile["source"] == "edited"
    assert profile["weights"]["technical_skills"] == 50
    # the ai still proposed key terms and its own weights, shown beside the edited ones
    assert profile["keywords"] and profile["suggested"]["weights"]["technical_skills"] == 56


def test_asking_the_ai_again_replaces_edited_weights(
    client: TestClient, interviewer_token: str, screened, inline_jobs, fake_scoring
):
    _score(client, interviewer_token, screened["id"], inline_jobs)
    headers = auth_header(interviewer_token)
    client.put(
        f"/api/v1/jobs/{screened['id']}/scoring/weights",
        json={"weights": {"jd_match": 100, "technical_skills": 0, "relevant_experience": 0}},
        headers=headers,
    )
    response = client.post(
        f"/api/v1/jobs/{screened['id']}/scoring/weights/suggest", headers=headers
    )
    assert response.status_code == 202
    assert inline_jobs[-1]["error"] is None
    profile = _results(client, interviewer_token, screened["id"])["profile"]
    assert profile["source"] == "ai" and profile["weights"]["technical_skills"] == 56


def test_ai_unavailable_for_weights_falls_back_to_standard_weights(
    client: TestClient, interviewer_token: str, screened, inline_jobs, fake_scoring, monkeypatch
):
    def failing(payload):
        raise ExternalServiceError("The AI service is busy or unavailable right now.")

    monkeypatch.setattr("app.worker.tasks._weights_proposer", lambda: failing)
    _, result = _score(client, interviewer_token, screened["id"], inline_jobs)
    assert result["scored"] == 2
    assert any("standard weights" in w for w in result["warnings"])
    profile = _results(client, interviewer_token, screened["id"])["profile"]
    assert profile["source"] == "default"
    assert profile["weights"]["technical_skills"] == 47 and profile["keywords"] == []


def test_a_new_screening_makes_the_score_stale(
    client: TestClient, interviewer_token: str, screened, inline_jobs, fake_scoring, db_session
):
    from datetime import UTC, datetime, timedelta

    from app.models.screening import Screening

    _score(client, interviewer_token, screened["id"], inline_jobs)
    s = db_session.scalars(select(Screening).where(Screening.rank == 1)).first()
    s.screened_at = datetime.now(UTC) + timedelta(minutes=1)
    db_session.commit()

    data = _results(client, interviewer_token, screened["id"])
    assert data["waiting_count"] == 1
    assert [r["stale"] for r in data["ranked"]].count(True) == 1
    started, result = _score(client, interviewer_token, screened["id"], inline_jobs)
    assert started["candidate_count"] == 1 and result["scored"] == 1
    # weights and key terms were set once and reused
    assert fake_scoring["proposer"] == 1


def test_nothing_to_score_is_explained(
    client: TestClient, interviewer_token: str, screened, inline_jobs, fake_scoring
):
    _score(client, interviewer_token, screened["id"], inline_jobs)
    response = client.post(
        f"/api/v1/jobs/{screened['id']}/scoring", json={}, headers=auth_header(interviewer_token)
    )
    assert response.status_code == 422
    assert "up-to-date score" in response.json()["error"]["message"]


def test_shortlist_and_select_for_interview(
    client: TestClient, interviewer_token: str, screened, inline_jobs, fake_scoring
):
    _score(client, interviewer_token, screened["id"], inline_jobs)
    headers = auth_header(interviewer_token)
    grace = _results(client, interviewer_token, screened["id"])["ranked"][1]["candidate_id"]
    url = f"/api/v1/jobs/{screened['id']}/scoring/{grace}"

    selected = client.patch(url, json={"selected_for_interview": True}, headers=headers).json()
    # selecting puts them on the shortlist too
    assert selected["is_shortlisted"] is True and selected["selected_at"]

    # a rescore keeps the people's decisions
    _score(client, interviewer_token, screened["id"], inline_jobs, rescore=True)
    row = _results(client, interviewer_token, screened["id"])["ranked"][1]
    assert row["is_shortlisted"] is True and row["selected_at"]

    removed = client.patch(url, json={"shortlisted": False}, headers=headers).json()
    assert removed["is_shortlisted"] is False and removed["selected_at"] is None


def test_scores_are_closed_to_candidates(client: TestClient, candidate_token: str, screened):
    headers = auth_header(candidate_token)
    base = f"/api/v1/jobs/{screened['id']}/scoring"
    assert client.get(base, headers=headers).status_code == 403
    assert client.get(f"{base}/1", headers=headers).status_code == 403
    assert client.post(base, json={}, headers=headers).status_code == 403
    assert client.put(f"{base}/weights", json={"weights": {}}, headers=headers).status_code == 403
    assert client.patch(f"{base}/1", json={}, headers=headers).status_code == 403


def test_score_profile_is_stored_on_the_job(
    client: TestClient, interviewer_token: str, screened, inline_jobs, fake_scoring, db_session
):
    _score(client, interviewer_token, screened["id"], inline_jobs)
    stored = db_session.get(Job, screened["id"])
    assert stored.score_profile["suggested"]["prompt_version"] == "weights-v2"


def test_overview_and_jobs_list_follow_scoring(
    client: TestClient, interviewer_token: str, screened, inline_jobs, fake_scoring
):
    headers = auth_header(interviewer_token)
    overview = client.get("/api/v1/overview", headers=headers).json()
    assert any(
        a["kind"] == "score" and "Score 2 candidates" in a["title"] for a in overview["attention"]
    )

    _score(client, interviewer_token, screened["id"], inline_jobs)
    ada = _results(client, interviewer_token, screened["id"])["ranked"][0]["candidate_id"]
    client.patch(
        f"/api/v1/jobs/{screened['id']}/scoring/{ada}",
        json={"selected_for_interview": True},
        headers=headers,
    )

    overview = client.get("/api/v1/overview", headers=headers).json()
    assert {k: overview["pipeline"][k] for k in ("scored", "shortlisted", "selected")} == {
        "scored": 2,
        "shortlisted": 1,
        "selected": 1,
    }
    assert not any(a["kind"] == "score" for a in overview["attention"])
    [listed] = client.get("/api/v1/jobs", headers=headers).json()
    assert (listed["scored_count"], listed["unscored_count"], listed["shortlisted_count"]) == (
        2,
        0,
        1,
    )
