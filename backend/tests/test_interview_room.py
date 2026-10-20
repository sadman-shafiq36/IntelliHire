import base64
import hashlib
import json
from datetime import UTC, datetime, timedelta

import httpx
import jwt
import pytest
from fastapi.testclient import TestClient
from livekit import api

from app.models.candidate import Candidate
from app.models.enums import InterviewStatus, SpeakerRole
from app.models.interview import Interview, InterviewParticipant, TranscriptTurn
from app.models.job import Job
from app.services.interviews import hash_token
from app.tools import http
from tests.conftest import auth_header

KEY = "APItest"
SECRET = "a-test-secret-that-is-long-enough-for-livekit"


@pytest.fixture
def livekit_server(monkeypatch: pytest.MonkeyPatch):
    from app.config import settings

    monkeypatch.setattr(settings, "livekit_api_key", KEY)
    monkeypatch.setattr(settings, "livekit_api_secret", SECRET)
    monkeypatch.setattr(settings, "livekit_public_url", "ws://localhost:7880")
    closed: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/twirp/livekit.RoomService/DeleteRoom"
        claims = jwt.decode(
            request.headers["Authorization"].split()[1], SECRET, algorithms=["HS256"]
        )
        assert claims["video"]["roomCreate"] is True
        closed.append(json.loads(request.content)["room"])
        return httpx.Response(200, json={})

    http.set_transport(httpx.MockTransport(handler))
    yield closed
    http.set_transport(None)


@pytest.fixture
def booked(db_session):
    job = Job(title="Backend Engineer")
    candidate = Candidate(full_name="Ada Lovelace", email="ada@example.com")
    db_session.add_all([job, candidate])
    db_session.flush()
    start = datetime.now(UTC) + timedelta(hours=1)
    interview = Interview(
        job_id=job.id,
        candidate_id=candidate.id,
        status=InterviewStatus.SCHEDULED,
        scheduled_start=start,
        scheduled_end=start + timedelta(minutes=45),
        timezone_name="Asia/Dhaka",
        join_token=hash_token("candidate-link"),
        join_token_expires_at=start + timedelta(days=1),
    )
    db_session.add(interview)
    db_session.commit()
    return interview


def _claims(token: str) -> dict:
    return jwt.decode(token, SECRET, algorithms=["HS256"])


def _open(client, token, interview_id):
    return client.post(f"/api/v1/interviews/{interview_id}/session", headers=auth_header(token))


def test_opening_the_room_starts_the_session_and_issues_a_token(
    client: TestClient, interviewer_token: str, booked, livekit_server, db_session
):
    response = _open(client, interviewer_token, booked.id)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["url"] == "ws://localhost:7880" and body["room"] == f"interview-{booked.id}"
    claims = _claims(body["token"])
    assert claims["sub"] == body["identity"] and body["identity"].startswith("interviewer-")
    assert claims["video"]["room"] == f"interview-{booked.id}"
    assert claims["video"]["roomJoin"] is True
    # only the transcriber can send data into the room
    assert claims["video"]["canPublishData"] is False

    db_session.refresh(booked)
    assert booked.status == InterviewStatus.IN_PROGRESS and booked.started_at is not None
    started = booked.started_at

    # opening it again rejoins the same session
    assert _open(client, interviewer_token, booked.id).status_code == 200
    db_session.refresh(booked)
    assert booked.started_at == started


def test_the_candidate_joins_only_once_the_room_is_open(
    client: TestClient, interviewer_token: str, booked, livekit_server
):
    early = client.post("/api/v1/interviews/join/candidate-link/session")
    assert early.status_code == 409
    assert "hasn't opened the room" in early.json()["error"]["message"]

    _open(client, interviewer_token, booked.id)
    joined = client.post("/api/v1/interviews/join/candidate-link/session")
    assert joined.status_code == 200, joined.text
    claims = _claims(joined.json()["token"])
    assert claims["sub"] == f"candidate-{booked.candidate_id}"
    assert claims["video"]["canPublishSources"] == ["camera", "microphone"]
    # livekit gets the role, never the candidate's name
    assert claims["name"] == "Candidate"
    assert claims["video"]["canPublishData"] is False

    assert client.post("/api/v1/interviews/join/wrong-link/session").status_code == 404
    # the public page now says the call is on
    assert client.get("/api/v1/interviews/join/candidate-link").json()["status"] == "in_progress"


def test_room_endpoints_are_for_interviewers_only(
    client: TestClient, candidate_token: str, booked, livekit_server
):
    for method, path in [
        ("post", f"/api/v1/interviews/{booked.id}/session"),
        ("get", f"/api/v1/interviews/{booked.id}/session"),
        ("post", f"/api/v1/interviews/{booked.id}/session/end"),
        ("get", f"/api/v1/interviews/{booked.id}/transcript"),
    ]:
        assert getattr(client, method)(path).status_code == 401
        response = getattr(client, method)(path, headers=auth_header(candidate_token))
        assert response.status_code == 403, path


def test_ending_closes_the_room_and_keeps_the_transcript(
    client: TestClient, interviewer_token: str, booked, livekit_server, db_session
):
    _open(client, interviewer_token, booked.id)
    db_session.add_all(
        [
            InterviewParticipant(
                interview_id=booked.id,
                role=SpeakerRole.CANDIDATE,
                identity=f"candidate-{booked.candidate_id}",
                joined_at=datetime.now(UTC),
            ),
            TranscriptTurn(
                interview_id=booked.id,
                sequence=1,
                speaker_role=SpeakerRole.CANDIDATE,
                speaker_label="Candidate",
                text="I built REST services in Django.",
                start_offset_ms=4000,
                end_offset_ms=6500,
                stt_provider="groq",
            ),
        ]
    )
    db_session.commit()

    response = client.post(
        f"/api/v1/interviews/{booked.id}/session/end", headers=auth_header(interviewer_token)
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "completed" and body["ended_at"] is not None
    assert body["transcription"]["state"] == "finished"
    # anyone still in the room is marked as having left
    assert body["attendance"][0]["left_at"] is not None
    assert livekit_server == [f"interview-{booked.id}"]

    transcript = client.get(
        f"/api/v1/interviews/{booked.id}/transcript", headers=auth_header(interviewer_token)
    ).json()
    assert [t["text"] for t in transcript["turns"]] == ["I built REST services in Django."]

    pipeline = client.get("/api/v1/overview", headers=auth_header(interviewer_token)).json()
    assert pipeline["pipeline"]["interviewed"] == 1
    assert pipeline["pipeline"]["interviews_live"] == 0

    # the room can't be reopened and the candidate's link no longer lets them in
    assert _open(client, interviewer_token, booked.id).status_code == 409
    late = client.post("/api/v1/interviews/join/candidate-link/session")
    assert late.status_code == 409 and "ended" in late.json()["error"]["message"]


def test_a_room_that_cannot_be_closed_still_ends_the_interview(
    client: TestClient, interviewer_token: str, booked, livekit_server, db_session
):
    _open(client, interviewer_token, booked.id)
    http.set_transport(httpx.MockTransport(lambda r: httpx.Response(503)))
    response = client.post(
        f"/api/v1/interviews/{booked.id}/session/end", headers=auth_header(interviewer_token)
    )
    assert response.status_code == 200
    db_session.refresh(booked)
    assert booked.status == InterviewStatus.COMPLETED


def test_transcript_is_ordered_by_time_and_can_be_read_incrementally(
    client: TestClient, interviewer_token: str, booked, db_session
):
    # saved in the order the speech service answered, not the order they were spoken
    for seq, (role, start, text) in enumerate(
        [
            (SpeakerRole.CANDIDATE, 9000, "Mostly Python."),
            (SpeakerRole.INTERVIEWER, 2000, "Which languages do you use?"),
            (SpeakerRole.INTERVIEWER, 15000, "Thanks."),
        ],
        start=1,
    ):
        db_session.add(
            TranscriptTurn(
                interview_id=booked.id,
                sequence=seq,
                speaker_role=role,
                speaker_label=role.value.title(),
                text=text,
                start_offset_ms=start,
                end_offset_ms=start + 1500,
            )
        )
    db_session.commit()
    url = f"/api/v1/interviews/{booked.id}/transcript"
    turns = client.get(url, headers=auth_header(interviewer_token)).json()["turns"]
    assert [t["text"] for t in turns] == [
        "Which languages do you use?",
        "Mostly Python.",
        "Thanks.",
    ]
    assert turns[0]["speaker_role"] == "interviewer"
    newer = client.get(f"{url}?after=2", headers=auth_header(interviewer_token)).json()["turns"]
    assert [t["sequence"] for t in newer] == [3]


def _signed(event: dict) -> tuple[str, dict[str, str]]:
    body = json.dumps(event)
    digest = base64.b64encode(hashlib.sha256(body.encode()).digest()).decode()
    token = api.AccessToken(KEY, SECRET).with_sha256(digest).to_jwt()
    return body, {"Authorization": token, "Content-Type": "application/webhook+json"}


def _event(name: str, interview_id: int, identity: str | None, at: int) -> dict:
    event = {"event": name, "room": {"name": f"interview-{interview_id}"}, "createdAt": str(at)}
    if identity:
        event["participant"] = {"identity": identity, "name": "Someone"}
    return event


def test_webhooks_record_joins_reconnects_and_leaves(
    client: TestClient, booked, livekit_server, db_session
):
    who = f"candidate-{booked.candidate_id}"
    t0 = int(datetime.now(UTC).timestamp())
    for name, identity, at in [
        ("participant_joined", who, t0),
        ("participant_joined", "transcriber", t0),
        ("participant_left", who, t0 + 60),
        # back after a dropped connection: a second row, the first one kept
        ("participant_joined", who, t0 + 90),
        ("room_finished", None, t0 + 600),
    ]:
        body, headers = _signed(_event(name, booked.id, identity, at))
        response = client.post("/api/v1/livekit/webhook", content=body, headers=headers)
        assert response.status_code == 200, response.text

    rows = db_session.query(InterviewParticipant).order_by(InterviewParticipant.id).all()
    assert [r.identity for r in rows] == [who, who]
    assert all(r.role == SpeakerRole.CANDIDATE and r.display_label == "Candidate" for r in rows)
    assert int(rows[0].left_at.replace(tzinfo=UTC).timestamp()) == t0 + 60
    assert int(rows[1].left_at.replace(tzinfo=UTC).timestamp()) == t0 + 600


def test_an_unsigned_or_altered_webhook_is_rejected(client: TestClient, booked, livekit_server):
    body, headers = _signed(
        _event("participant_joined", booked.id, "candidate-1", int(datetime.now(UTC).timestamp()))
    )
    tampered = body.replace("candidate-1", "interviewer-1")
    assert (
        client.post("/api/v1/livekit/webhook", content=tampered, headers=headers).status_code == 401
    )
    assert client.post("/api/v1/livekit/webhook", content=body).status_code == 401


def test_a_candidate_in_a_live_interview_cannot_be_deleted(
    client: TestClient, interviewer_token: str, booked, livekit_server
):
    _open(client, interviewer_token, booked.id)
    response = client.delete(
        f"/api/v1/candidates/{booked.candidate_id}", headers=auth_header(interviewer_token)
    )
    assert response.status_code == 409
    assert "End this candidate's interview" in response.json()["error"]["message"]
