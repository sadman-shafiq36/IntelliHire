from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models.candidate import Application, Candidate, CvDocument, CvExtraction
from app.models.screening import CandidateScore, ScoreDimension, Screening
from tests.conftest import auth_header
from tests.test_scoring_api import _results, _score, fake_scoring, screened  # noqa: F401
from tests.test_screening_api import fake_screening, job  # noqa: F401


@pytest.fixture
def deleted_threads(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    seen: list[str] = []
    monkeypatch.setattr("app.services.candidates._delete_checkpoints", seen.extend)
    return seen


def test_deleting_a_candidate_removes_all_their_data(
    client: TestClient,
    interviewer_token: str,
    screened,  # noqa: F811
    inline_jobs,
    fake_scoring,  # noqa: F811
    db_session,
    deleted_threads,
):
    _score(client, interviewer_token, screened["id"], inline_jobs)
    ada = db_session.scalar(select(Candidate).where(Candidate.email == "ada@example.com"))
    ada_id = ada.id
    docs = db_session.scalars(select(CvDocument).where(CvDocument.candidate_id == ada_id)).all()
    doc_ids = [d.id for d in docs]
    files = [Path(d.storage_path) for d in docs]
    assert files and all(f.exists() for f in files)

    response = client.delete(f"/api/v1/candidates/{ada_id}", headers=auth_header(interviewer_token))
    assert response.status_code == 204
    db_session.expire_all()

    assert db_session.get(Candidate, ada_id) is None
    assert not db_session.scalars(select(CvDocument).where(CvDocument.id.in_(doc_ids))).all()
    assert not db_session.scalars(
        select(CvExtraction).where(CvExtraction.cv_document_id.in_(doc_ids))
    ).all()
    assert not db_session.scalars(
        select(Application).where(Application.candidate_id == ada_id)
    ).all()
    assert not db_session.scalars(select(Screening).where(Screening.candidate_id == ada_id)).all()
    assert not db_session.scalars(
        select(CandidateScore).where(CandidateScore.candidate_id == ada_id)
    ).all()
    assert all(not f.exists() for f in files)
    # the screening and scoring checkpoints hold her cv text
    assert any(t.startswith("screen-") for t in deleted_threads)
    assert any(t.startswith("score-") for t in deleted_threads)

    # the others move up and keep their data
    ranked = _results(client, interviewer_token, screened["id"])["ranked"]
    assert [(r["full_name"], r["rank"]) for r in ranked] == [("Grace Hopper", 1)]
    screening = client.get(
        f"/api/v1/jobs/{screened['id']}/screening", headers=auth_header(interviewer_token)
    ).json()
    assert [r["rank"] for r in screening["ranked"]] == [1]
    assert db_session.scalars(select(ScoreDimension)).all()


def test_nothing_is_deleted_if_the_checkpoints_cannot_be(
    client: TestClient,
    interviewer_token: str,
    screened,  # noqa: F811
    db_session,
    monkeypatch,
):
    def broken(thread_ids):
        raise OSError("database unreachable")

    monkeypatch.setattr("app.services.candidates._delete_checkpoints", broken)
    ada = db_session.scalar(select(Candidate).where(Candidate.email == "ada@example.com"))
    response = client.delete(f"/api/v1/candidates/{ada.id}", headers=auth_header(interviewer_token))
    assert response.status_code == 503
    body = response.json()["error"]
    assert "Nothing was removed" in body["message"]
    assert "database unreachable" not in str(body)
    db_session.expire_all()
    assert db_session.get(Candidate, ada.id) is not None


def test_deleting_twice_or_as_a_candidate_is_refused(
    client: TestClient,
    interviewer_token: str,
    candidate_token: str,
    screened,  # noqa: F811
    deleted_threads,
):
    assert (
        client.delete("/api/v1/candidates/1", headers=auth_header(candidate_token)).status_code
        == 403
    )
    first = client.delete("/api/v1/candidates/1", headers=auth_header(interviewer_token))
    assert first.status_code == 204
    again = client.delete("/api/v1/candidates/1", headers=auth_header(interviewer_token))
    assert again.status_code == 404
