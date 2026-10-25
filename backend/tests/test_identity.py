import base64
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models.candidate import Candidate, CvDocument, CvExtraction
from app.models.enums import (
    InterviewStatus,
    RequirementCategory,
    RequirementMatchStatus,
    ScreeningStatus,
)
from app.models.identity import BiometricConsent, FaceReference, IdentityCheck
from app.models.interview import Interview
from app.models.job import Job, JobRequirement
from app.models.screening import CandidateScore, Screening, ScreeningRequirementResult
from app.services import applications, identity
from app.services.candidates import delete_candidate
from app.services.interviews import hash_token
from app.tools import face
from app.tools.google_forms import Question
from tests.conftest import auth_header

LINK = "link-for-ada"


def vec(i: int, noise: float = 0.0, j: int | None = None) -> list[float]:
    # unit vectors: the same i is the same face, a different one is a stranger
    v = [0.0] * 128
    v[i] = 1.0
    if j is not None:
        v[j] = noise
    norm = sum(x * x for x in v) ** 0.5
    return [x / norm for x in v]


def faces(*vectors) -> list[face.Face]:
    return [face.Face(v, None) for v in vectors]


# --- the decision ------------------------------------------------------------------------


def test_the_match_decides_conservatively():
    me, other, stranger = vec(0), vec(1), vec(2)
    assert identity.decide(me, [other], me)["status"] == "confirmed"
    # close to the photo on file, but not enough to be sure
    weak = vec(0, noise=2.3, j=3)
    assert 0.363 <= identity.decide(me, [], weak)["score"] < 0.45
    assert identity.decide(me, [], weak)["status"] == "low_confidence"
    # looks like someone else who applied
    result = identity.decide(me, [other], other)
    assert result["status"] == "mismatch" and result["reason"] == "other"
    nobody = identity.decide(me, [other], stranger)
    assert nobody["status"] == "not_identified" and nobody["reason"] == "no_match"


def test_the_template_is_stored_encrypted():
    blob = identity._pack(vec(5))
    assert b"\x00\x00\x80?" not in blob  # 1.0 as a little-endian float
    assert identity._unpack(blob)[5] == pytest.approx(1.0)


# --- the candidate's page ----------------------------------------------------------------


@pytest.fixture
def booked(db_session):
    job = Job(title="Backend Engineer")
    db_session.add(job)
    db_session.flush()
    req = JobRequirement(job_id=job.id, text="Django web development", is_mandatory=True,
                         category=RequirementCategory.TECHNICAL_SKILL)  # fmt: skip
    k8s = JobRequirement(job_id=job.id, text="Kubernetes in production",
                         category=RequirementCategory.TECHNICAL_SKILL)  # fmt: skip
    ada = Candidate(full_name="Ada Lovelace", email="ada@example.com")
    bob = Candidate(full_name="Bob Example", email="bob@example.com")
    db_session.add_all([req, k8s, ada, bob])
    db_session.flush()
    doc = CvDocument(candidate_id=ada.id, job_id=job.id, original_filename="cv.pdf",
                     storage_path="/tmp/none.pdf", content_type="application/pdf",
                     size_bytes=1)  # fmt: skip
    db_session.add(doc)
    db_session.flush()
    structured = {
        "headline": "Backend engineer",
        "total_years_experience": 5,
        "skills": ["Django", "PostgreSQL"],
        "work_history": [{"title": "Engineer", "organisation": "Acme"}],
    }
    db_session.add(
        CvExtraction(
            cv_document_id=doc.id, raw_text="Built Django services.", structured=structured
        )
    )
    screening = Screening(job_id=job.id, candidate_id=ada.id, cv_document_id=doc.id,
                          status=ScreeningStatus.SCREENED,
                          summary="Strong Django work.")  # fmt: skip
    db_session.add(screening)
    db_session.flush()
    db_session.add_all([
        ScreeningRequirementResult(screening_id=screening.id, job_requirement_id=req.id,
                                   status=RequirementMatchStatus.MET,
                                   evidence_quote="Built Django services", evidence_verified=True),
        ScreeningRequirementResult(screening_id=screening.id, job_requirement_id=k8s.id,
                                   status=RequirementMatchStatus.UNMET),
    ])  # fmt: skip
    db_session.add(CandidateScore(job_id=job.id, candidate_id=ada.id, aggregate_score=81.0, rank=1))
    start = datetime.now(UTC) + timedelta(hours=1)
    interview = Interview(job_id=job.id, candidate_id=ada.id, status=InterviewStatus.SCHEDULED,
                          scheduled_start=start, scheduled_end=start + timedelta(minutes=45),
                          join_token=hash_token(LINK),
                          join_token_expires_at=start + timedelta(days=1))  # fmt: skip
    db_session.add(interview)
    db_session.commit()
    return {"interview": interview, "ada": ada, "bob": bob, "job": job}


def photo(text: bytes = b"jpeg") -> str:
    return "data:image/jpeg;base64," + base64.b64encode(text).decode()


@pytest.fixture
def fake_face(monkeypatch: pytest.MonkeyPatch):
    # the bytes say which face is in the picture
    def embed(data: bytes) -> face.Face:
        if data == b"nobody":
            return face.Face(None, "no_face")
        if data == b"crowd":
            return face.Face(None, "several_faces")
        return face.Face(vec(int(data.decode().removeprefix("face"))), None)

    monkeypatch.setattr(face, "embed_image", embed)


def test_consent_and_a_photo_on_the_candidates_page(
    client: TestClient, booked, fake_face, db_session
):
    base = f"/api/v1/interviews/join/{LINK}/identity"
    first = client.get(base).json()
    assert first["consent"] is None and "never the photo itself" in first["text"]

    bad = client.post(f"{base}/photo", json={"images": [photo(b"nobody")]})
    assert bad.status_code == 422 and "couldn't see a face" in bad.json()["error"]["message"]
    crowd = client.post(f"{base}/photo", json={"images": [photo(b"crowd")]})
    assert "Make sure only you" in crowd.json()["error"]["message"]

    given = client.post(f"{base}/photo", json={"images": [photo(b"face0"), photo(b"nobody")]})
    assert given.status_code == 200, given.text
    assert given.json()["consent"] == "given" and given.json()["reference"] == "ready"
    ref = db_session.scalar(select(FaceReference))
    assert ref.source == "selfie" and identity._unpack(ref.embedding)[0] == pytest.approx(1.0)
    # kept until a few days after the interview
    assert ref.expires_at.replace(tzinfo=UTC) > datetime.now(UTC) + timedelta(days=6)

    withdrawn = client.post(f"{base}/decline").json()
    assert withdrawn["consent"] == "withdrawn" and withdrawn["reference"] is None
    decisions = [c.decision for c in db_session.scalars(select(BiometricConsent))]
    assert decisions == ["given", "withdrawn"]
    assert client.get("/api/v1/interviews/join/not-a-link/identity").status_code == 404


# --- matching, the interviewer and the profile -------------------------------------------


def enrol(db_session, candidate, vector):
    identity._record(db_session, candidate.id, "given", "join_page")
    db_session.add(FaceReference(candidate_id=candidate.id, source="selfie", status="ready",
                                 embedding=identity._pack(vector),
                                 expires_at=datetime.now(UTC) + timedelta(days=3)))  # fmt: skip
    db_session.commit()


def test_without_consent_the_face_is_not_looked_at(booked, db_session):
    iv = booked["interview"]
    assert identity.references_for(db_session, iv.id) is None
    row = identity.current_row(db_session, iv.id)
    assert row.status == "not_identified" and row.reason == "no_consent"
    identity._record(db_session, booked["ada"].id, "given", "form")
    db_session.commit()
    assert identity.references_for(db_session, iv.id) is None
    assert identity.current_row(db_session, iv.id).reason == "no_reference"


def test_match_then_profile(client: TestClient, interviewer_token, booked, db_session):
    iv = booked["interview"]
    h = auth_header(interviewer_token)
    enrol(db_session, booked["ada"], vec(0))
    enrol(db_session, booked["bob"], vec(1))

    assert (
        client.get(f"/api/v1/interviews/{iv.id}/identity", headers=h).json()["status"] == "pending"
    )
    # the profile stays closed until identity is settled
    assert client.get(f"/api/v1/interviews/{iv.id}/profile", headers=h).status_code == 409

    identity.record_match(db_session, iv.id, faces(vec(1), vec(1)))
    seen = client.get(f"/api/v1/interviews/{iv.id}/identity", headers=h).json()
    assert seen["status"] == "mismatch" and "different candidate" in seen["message"]
    assert seen["profile_available"] is False

    identity.record_match(db_session, iv.id, [face.Face(None, "no_face")] * 3)
    assert identity.current_row(db_session, iv.id).reason == "no_face"

    identity.record_match(db_session, iv.id, faces(vec(0), vec(0, 0.2, 5)))
    seen = client.get(f"/api/v1/interviews/{iv.id}/identity", headers=h).json()
    assert seen["status"] == "confirmed" and seen["profile_available"] is True

    profile = client.get(f"/api/v1/interviews/{iv.id}/profile", headers=h).json()
    assert profile["headline"] == "Backend engineer" and profile["score"] == 81.0
    assert profile["requirements_met"] == [
        {"text": "Django web development", "quote": "Built Django services"}
    ]
    assert [p["text"] for p in profile["probe"]] == ["Kubernetes in production"]
    # no contact details in it
    assert "ada@example.com" not in str(profile)


def test_the_interviewer_decides_when_the_match_cant(
    client: TestClient, interviewer_token, candidate_token, booked, db_session
):
    iv = booked["interview"]
    h = auth_header(interviewer_token)
    identity.references_for(db_session, iv.id)  # no consent: not identified
    confirmed = client.post(
        f"/api/v1/interviews/{iv.id}/identity/decision", json={"decision": "confirm"}, headers=h
    ).json()
    assert confirmed["status"] == "manually_confirmed" and confirmed["profile_available"]
    assert client.get(f"/api/v1/interviews/{iv.id}/profile", headers=h).status_code == 200
    rejected = client.post(
        f"/api/v1/interviews/{iv.id}/identity/decision", json={"decision": "reject"}, headers=h
    ).json()
    assert rejected["status"] == "rejected" and not rejected["profile_available"]
    # retry only while the interview runs
    assert client.post(f"/api/v1/interviews/{iv.id}/identity/retry", headers=h).status_code == 409

    c = auth_header(candidate_token)
    for path in (f"/api/v1/interviews/{iv.id}/identity", f"/api/v1/interviews/{iv.id}/profile"):
        assert client.get(path, headers=c).status_code == 403
    assert (
        client.post(
            f"/api/v1/interviews/{iv.id}/identity/decision", json={"decision": "confirm"}, headers=c
        ).status_code
        == 403
    )


# --- the form photo, retention and deletion ----------------------------------------------


def test_the_form_photo_and_its_own_consent_are_told_apart():
    mapping = applications.map_questions(
        [
            Question("q1", "Full name", "text"),
            Question("q2", "Upload your CV", "file"),
            Question("q3", "A recent photo of you (optional)", "file"),
            Question("q4", "I agree to my photo being used to confirm my identity", "choice"),
            Question("q5", "I agree to my data being processed", "choice"),
        ]
    )
    assert mapping.cv_question == "q2" and mapping.photo_question == "q3"
    assert mapping.fields["photo_consent"] == "q4" and mapping.fields["consent"] == "q5"


def test_a_form_photo_waits_then_becomes_a_template(booked, fake_face, db_session):
    ada, bob = booked["ada"], booked["bob"]
    deleted: list[str] = []
    # bob sent a photo but said no: it is deleted unused
    identity.record_form_photo(db_session, bob.id, "drive-bob", True, False, deleted.append)
    # ada said yes: nothing is downloaded yet
    identity.record_form_photo(db_session, ada.id, "drive-ada", True, True, deleted.append)
    db_session.commit()
    assert deleted == ["drive-bob"]
    assert identity.latest_consent(db_session, bob.id) == "declined"
    assert identity.needs_preparing(db_session, ada.id)

    downloads: list[str] = []
    status = identity.prepare_reference(
        db_session, ada.id, lambda fid: downloads.append(fid) or b"face0", deleted.append
    )
    assert status == "ready" and downloads == ["drive-ada"]
    # the photo is deleted from drive once its template exists
    assert deleted == ["drive-bob", "drive-ada"]
    ref = db_session.scalar(select(FaceReference).where(FaceReference.candidate_id == ada.id))
    assert ref.drive_file_id is None and ref.embedding


def test_templates_expire_and_go_with_the_candidate(booked, db_session):
    ada, bob = booked["ada"], booked["bob"]
    enrol(db_session, ada, vec(0))
    enrol(db_session, bob, vec(1))
    old = db_session.scalar(select(FaceReference).where(FaceReference.candidate_id == bob.id))
    old.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    db_session.commit()
    assert identity.sweep(db_session, lambda fid: None) == 1
    assert [r.candidate_id for r in db_session.scalars(select(FaceReference))] == [ada.id]

    iv = booked["interview"]
    identity.record_match(db_session, iv.id, faces(vec(0)))
    iv.status = InterviewStatus.COMPLETED
    db_session.commit()
    delete_candidate(db_session, ada.id, delete_checkpoints=lambda threads: None)
    for model in (FaceReference, IdentityCheck):
        assert db_session.scalars(select(model)).all() == []
    assert (
        db_session.scalars(
            select(BiometricConsent).where(BiometricConsent.candidate_id == ada.id)
        ).all()
        == []
    )
