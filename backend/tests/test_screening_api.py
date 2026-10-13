import io
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.agents.screening.graph import build_screening_graph
from app.models.analysis import AgentRun
from app.models.candidate import Application, Candidate, CvDocument, CvExtraction
from tests import cv_fixtures as fx
from tests.conftest import auth_header

JOB = {
    "title": "Backend Engineer",
    "min_years_experience": 3,
    "requirements": [
        {"text": "Python web development", "is_mandatory": True, "weight": 2},
        {"text": "Relational databases such as PostgreSQL"},
        {"text": "Kubernetes in production"},
    ],
}

# what the fake reads from each cv, keyed by a word in the file
PEOPLE = {
    "ada": ("Ada Lovelace", "ada@example.com", ["met", "met", "unmet"]),
    "grace": ("Grace Hopper", "grace@example.com", ["met", "partially_met", "unmet"]),
    "linus": ("Linus Frontend", "linus@example.com", ["unmet", "met", "unmet"]),
}
QUOTES = [
    "Built REST services in Django for the online shop.",
    "Designed PostgreSQL schemas",
    "",
]


def fake_extractor(text):
    for key, (name, email, _) in PEOPLE.items():
        if email in text:
            return {"full_name": name, "email": email, "phone": "", "location": "",
                    "headline": f"{key} headline", "summary": "", "total_years_experience": 5,
                    "skills": [], "education": [], "work_history": [], "certifications": [],
                    "projects": []}, "fake-model"  # fmt: skip
    raise AssertionError("unknown cv")


@pytest.fixture
def job(client: TestClient, interviewer_token: str) -> dict:
    return client.post("/api/v1/jobs", json=JOB, headers=auth_header(interviewer_token)).json()


@pytest.fixture
def fake_screening(monkeypatch: pytest.MonkeyPatch):
    state = {}

    def extractor(text):
        result = fake_extractor(text)
        # remember who this was so the matcher answers for the same person
        state.setdefault("order", []).append(result[0]["email"])
        return result

    def matcher(job, requirements, cv, feedback):
        email = state["order"].pop(0)
        statuses = next(p[2] for p in PEOPLE.values() if p[1] == email)
        return {
            "matches": [
                {
                    "requirement_id": req["id"],
                    "status": status,
                    "evidence_quote": quote if status != "unmet" else "",
                    "reasoning": f"{status} for test",
                    "confidence": 0.8,
                }
                for req, status, quote in zip(requirements, statuses, QUOTES, strict=True)
            ],
            "summary": "Test summary.",
        }, "fake-model"

    @contextmanager
    def graph():
        yield build_screening_graph(extractor=extractor, matcher=matcher)

    monkeypatch.setattr("app.worker.tasks._screening_graph", graph)
    # one at a time, so the fake's order bookkeeping holds
    monkeypatch.setattr("app.config.settings.screening_concurrency", 1)
    return state


def _upload(client, token, job_id, files):
    return client.post(
        f"/api/v1/jobs/{job_id}/cvs",
        headers=auth_header(token),
        files=[
            ("files", (name, io.BytesIO(data), "application/octet-stream")) for name, data in files
        ],
    )


def _screen(client, token, job_id, inline_jobs, rescreen=False):
    response = client.post(
        f"/api/v1/jobs/{job_id}/screening", json={"rescreen": rescreen}, headers=auth_header(token)
    )
    assert response.status_code == 202, response.text
    assert inline_jobs[-1]["error"] is None, inline_jobs[-1]["error"]
    return inline_jobs[-1]["result"]


def _results(client, token, job_id):
    return client.get(f"/api/v1/jobs/{job_id}/screening", headers=auth_header(token)).json()


def test_upload_checks_each_file_on_its_own(client: TestClient, interviewer_token: str, job):
    response = _upload(
        client,
        interviewer_token,
        job["id"],
        [
            ("ada.pdf", fx.pdf()),
            ("photo.png", b"png"),
            ("empty.pdf", b""),
            ("g.docx", fx.docx_bytes()),
        ],
    )
    assert response.status_code == 201
    body = response.json()
    assert [a["filename"] for a in body["accepted"]] == ["ada.pdf", "g.docx"]
    reasons = {r["filename"]: r["reason"] for r in body["rejected"]}
    assert "PDF and Word" in reasons["photo.png"]
    assert "empty" in reasons["empty.pdf"]

    listed = client.get(
        f"/api/v1/jobs/{job['id']}/cvs", headers=auth_header(interviewer_token)
    ).json()
    assert {d["status"] for d in listed} == {"pending"}
    assert {d["source"] for d in listed} == {"upload"}


def test_screening_ranks_excludes_and_reports_unreadable_cvs(
    client: TestClient, interviewer_token: str, job, inline_jobs, fake_screening, db_session
):
    _upload(
        client,
        interviewer_token,
        job["id"],
        [
            ("ada.pdf", fx.pdf("Ada Lovelace", "ada@example.com")),
            ("grace.pdf", fx.pdf("Grace Hopper", "grace@example.com")),
            ("linus.pdf", fx.pdf("Linus Frontend", "linus@example.com")),
            ("broken.pdf", fx.pdf()[:300]),
            ("spanish.pdf", fx.spanish_pdf()),
        ],
    )
    result = _screen(client, interviewer_token, job["id"], inline_jobs)
    assert result["total"] == 5
    assert result["screened"] == 2 and result["excluded"] == 1
    assert result["unreadable"] == 1 and result["not_english"] == 1

    data = _results(client, interviewer_token, job["id"])
    ranked = data["ranked"]
    assert [r["full_name"] for r in ranked] == ["Ada Lovelace", "Grace Hopper"]
    assert [r["rank"] for r in ranked] == [1, 2]
    assert ranked[0]["fulfilment_percent"] == 75.0
    assert ranked[1]["fulfilment_percent"] == 62.5
    assert ranked[0]["must_have_met"] == 1 and ranked[0]["must_have_total"] == 1

    [excluded] = data["excluded"]
    assert excluded["full_name"] == "Linus Frontend"
    assert "Python web development" in excluded["exclusion_reason"]
    assert excluded["rank"] is None

    reasons = {n["filename"]: n["reason"] for n in data["not_screened"]}
    assert "damaged" in reasons["broken.pdf"]
    assert "English" in reasons["spanish.pdf"]
    assert data["pending_count"] == 0

    # candidates and applications come from the cv itself
    ada = db_session.scalar(select(Candidate).where(Candidate.email == "ada@example.com"))
    assert ada.full_name == "Ada Lovelace"
    assert (
        db_session.scalar(select(Application).where(Application.candidate_id == ada.id)).source
        == "direct_upload"
    )
    # only redacted text is stored
    texts = db_session.scalars(select(CvExtraction.raw_text)).all()
    assert texts and all("ada@example.com" not in t and "Lovelace" not in t for t in texts)
    # one audit row per cv
    assert (
        len(db_session.scalars(select(AgentRun).where(AgentRun.graph_name == "cv_screening")).all())
        == 5
    )


def test_candidate_detail_shows_evidence_per_requirement(
    client: TestClient, interviewer_token: str, job, inline_jobs, fake_screening
):
    _upload(
        client,
        interviewer_token,
        job["id"],
        [("ada.pdf", fx.pdf("Ada Lovelace", "ada@example.com"))],
    )
    _screen(client, interviewer_token, job["id"], inline_jobs)
    ada_id = _results(client, interviewer_token, job["id"])["ranked"][0]["candidate_id"]

    detail = client.get(
        f"/api/v1/jobs/{job['id']}/screening/{ada_id}", headers=auth_header(interviewer_token)
    ).json()
    assert detail["candidate"]["email"] == "ada@example.com"
    assert detail["model_name"] == "fake-model"
    first = detail["requirements"][0]
    assert first["text"] == "Python web development"
    assert first["status"] == "met"
    assert first["evidence_quote"] == "Built REST services in Django for the online shop."
    assert first["evidence_verified"] is True
    assert detail["requirements"][2]["status"] == "unmet"
    assert detail["cv"]["filename"] == "ada.pdf"

    cv = client.get(
        f"/api/v1/cv-documents/{detail['cv']['document_id']}/file",
        headers=auth_header(interviewer_token),
    )
    assert cv.status_code == 200 and cv.content.startswith(b"%PDF")


def test_a_form_applicant_is_screened_as_the_same_candidate(
    client: TestClient, interviewer_token: str, job, inline_jobs, fake_screening, db_session
):
    from app.models.enums import ExtractionStatus
    from app.services.screening import upload_cvs

    candidate = Candidate(full_name="Ada Lovelace", email="ada@example.com")
    db_session.add(candidate)
    db_session.commit()
    upload_cvs(db_session, job["id"], [("ada.pdf", "application/pdf", fx.pdf())], user_id=1)
    doc = db_session.scalar(select(CvDocument))
    doc.candidate_id = candidate.id
    doc.source = "google_form"
    assert doc.status == ExtractionStatus.PENDING
    db_session.commit()

    _screen(client, interviewer_token, job["id"], inline_jobs)
    assert db_session.scalars(select(Candidate)).all() == [candidate]
    assert (
        _results(client, interviewer_token, job["id"])["ranked"][0]["candidate_id"] == candidate.id
    )


def test_nothing_to_screen_is_explained(client: TestClient, interviewer_token: str, job):
    response = client.post(
        f"/api/v1/jobs/{job['id']}/screening", json={}, headers=auth_header(interviewer_token)
    )
    assert response.status_code == 422
    assert "no new CVs" in response.json()["error"]["message"]


def test_rescreen_runs_every_readable_cv_again(
    client: TestClient, interviewer_token: str, job, inline_jobs, fake_screening
):
    files = [("ada.pdf", fx.pdf("Ada Lovelace", "ada@example.com")), ("broken.pdf", fx.pdf()[:300])]
    _upload(client, interviewer_token, job["id"], files)
    _screen(client, interviewer_token, job["id"], inline_jobs)
    result = _screen(client, interviewer_token, job["id"], inline_jobs, rescreen=True)
    # the unreadable file isn't retried, the screened one is
    assert result["total"] == 1
    assert len(_results(client, interviewer_token, job["id"])["ranked"]) == 1


def test_candidates_cannot_upload_or_see_screening(client: TestClient, candidate_token: str, job):
    headers = auth_header(candidate_token)
    assert _upload(client, candidate_token, job["id"], [("a.pdf", fx.pdf())]).status_code == 403
    assert client.get(f"/api/v1/jobs/{job['id']}/screening", headers=headers).status_code == 403
    assert client.get(f"/api/v1/jobs/{job['id']}/cvs", headers=headers).status_code == 403
    response = client.post(f"/api/v1/jobs/{job['id']}/screening", json={}, headers=headers)
    assert response.status_code == 403


def test_a_cv_that_hits_a_busy_ai_service_is_tried_again(
    client: TestClient, interviewer_token: str, job, inline_jobs, monkeypatch, db_session
):
    from app.errors import ExternalServiceError

    calls = []

    def extractor(text):
        calls.append(1)
        if len(calls) == 1:
            raise ExternalServiceError("The AI service is busy or unavailable right now.")
        return fake_extractor(text)

    def matcher(job, requirements, cv, feedback):
        statuses = ["met", "met", "unmet"]
        matches = [
            {
                "requirement_id": r["id"],
                "status": status,
                "evidence_quote": quote if status != "unmet" else "",
                "reasoning": "",
                "confidence": 0.8,
            }
            for r, status, quote in zip(requirements, statuses, QUOTES, strict=True)
        ]
        return {"matches": matches, "summary": ""}, "fake-model"

    @contextmanager
    def graph():
        yield build_screening_graph(extractor=extractor, matcher=matcher)

    monkeypatch.setattr("app.worker.tasks._screening_graph", graph)
    monkeypatch.setattr("app.services.screening.seconds_until_available", lambda: 600.0)
    waits = []
    monkeypatch.setattr("app.services.screening.time.sleep", waits.append)
    _upload(
        client,
        interviewer_token,
        job["id"],
        [("ada.pdf", fx.pdf("Ada Lovelace", "ada@example.com"))],
    )
    result = _screen(client, interviewer_token, job["id"], inline_jobs)

    assert len(calls) == 2
    assert result["screened"] == 1 and result["failed"] == 0
    outcomes = [r.context["outcome"] for r in db_session.scalars(select(AgentRun))]
    assert sorted(outcomes) == ["retried", "screened"]
    # every model was rate limited, so the retry waited (capped) before going again
    assert waits == [90.0]


def test_cvs_left_by_a_dead_batch_are_screened_next_time(
    client: TestClient, interviewer_token: str, job, inline_jobs, fake_screening, db_session
):
    from app.models.enums import ExtractionStatus

    _upload(
        client,
        interviewer_token,
        job["id"],
        [("ada.pdf", fx.pdf("Ada Lovelace", "ada@example.com"))],
    )
    doc = db_session.scalar(select(CvDocument))
    # as left by a worker that died half way
    doc.status = ExtractionStatus.EXTRACTING
    db_session.commit()

    result = _screen(client, interviewer_token, job["id"], inline_jobs)
    assert result["total"] == 1 and result["screened"] == 1


def test_a_cv_that_failed_on_a_busy_service_waits_for_the_next_screening(
    client: TestClient, interviewer_token: str, job, inline_jobs, fake_screening, db_session
):
    from app.models.enums import ExtractionStatus

    headers = {"Authorization": f"Bearer {interviewer_token}"}
    _upload(
        client,
        interviewer_token,
        job["id"],
        [("ada.pdf", fx.pdf("Ada Lovelace", "ada@example.com"))],
    )
    doc = db_session.scalar(select(CvDocument))
    doc.status = ExtractionStatus.FAILED
    db_session.commit()

    body = client.get(f"/api/v1/jobs/{job['id']}/screening", headers=headers).json()
    assert body["pending_count"] == 1
    result = _screen(client, interviewer_token, job["id"], inline_jobs)
    assert result["total"] == 1 and result["screened"] == 1


def test_an_aborted_batch_puts_unfinished_cvs_back_in_the_queue(
    interviewer_token: str, client: TestClient, job, fake_screening, db_session, monkeypatch
):
    from app.agents.screening.graph import build_screening_graph as build
    from app.models.enums import ExtractionStatus
    from app.services import screening as service

    files = [
        ("ada.pdf", fx.pdf("Ada Lovelace", "ada@example.com")),
        ("grace.pdf", fx.pdf("Grace Hopper", "grace@example.com")),
    ]
    _upload(client, interviewer_token, job["id"], files)

    def explode(*args, **kwargs):
        # stands in for the job timeout firing while results are being saved
        raise TimeoutError("job timed out")

    monkeypatch.setattr(service, "_save", explode)
    graph = build(
        extractor=fake_extractor, matcher=lambda *a: ({"matches": [], "summary": ""}, "m")
    )
    with pytest.raises(TimeoutError):
        service.screen_job(db_session, job["id"], graph, concurrency=1)

    statuses = set(db_session.scalars(select(CvDocument.status)))
    assert statuses == {ExtractionStatus.PENDING}
