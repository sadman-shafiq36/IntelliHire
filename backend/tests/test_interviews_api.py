from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.agents.scheduling.graph import build_scheduling_graph
from app.models.analysis import AgentRun
from app.models.interview import Interview
from tests.conftest import auth_header
from tests.test_scheduling_graph import FakeTools, good_drafter
from tests.test_scoring_api import _results, _score, fake_scoring, screened  # noqa: F401
from tests.test_screening_api import fake_screening, job  # noqa: F401

TZ = "Asia/Dhaka"


def _tomorrow_at(hour: int) -> str:
    day = datetime.now(UTC) + timedelta(days=2)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day.strftime(f"%Y-%m-%dT{hour:02d}:00")


@pytest.fixture
def runtime(monkeypatch: pytest.MonkeyPatch):
    made: list[FakeTools] = []
    box = {"channel": "google", "busy": [], "fail": set()}

    def make_tools(token, mail_from=None):
        tools = FakeTools(channel="google" if token else "smtp", busy=box["busy"], fail=box["fail"])
        made.append(tools)
        return tools

    @contextmanager
    def fake_runtime():
        def drafter(payload):
            box.setdefault("kinds", []).append(payload["kind"])
            return good_drafter(payload)

        def make_graph(tools, contact):
            box["contact"] = contact
            return build_scheduling_graph(tools, contact, drafter=drafter)

        yield make_graph, make_tools

    monkeypatch.setattr("app.worker.tasks._scheduling_runtime", fake_runtime)
    box["made"] = made
    return box


@pytest.fixture
def google_connected(db_session):
    from app.models.enums import IntegrationProvider
    from app.models.integration import IntegrationAccount
    from app.services.integrations import encrypt
    from app.tools.google_forms import SCOPES

    db_session.add(
        IntegrationAccount(
            provider=IntegrationProvider.GOOGLE,
            account_id="sam@example.com",
            access_token_enc=encrypt("g-token"),
            scopes=SCOPES,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    db_session.commit()


@pytest.fixture
def selected(client, interviewer_token, screened, inline_jobs, fake_scoring):  # noqa: F811
    _score(client, interviewer_token, screened["id"], inline_jobs)
    ada = _results(client, interviewer_token, screened["id"])["ranked"][0]
    client.patch(
        f"/api/v1/jobs/{screened['id']}/scoring/{ada['candidate_id']}",
        json={"selected_for_interview": True},
        headers=auth_header(interviewer_token),
    )
    return {"job_id": screened["id"], "candidate_id": ada["candidate_id"]}


def _schedule(client, token, selected, inline_jobs, **options):
    body = {"candidate_id": selected["candidate_id"], "duration_minutes": 45, "time_zone": TZ}
    body.update(options)
    response = client.post(
        f"/api/v1/jobs/{selected['job_id']}/interviews", json=body, headers=auth_header(token)
    )
    return response


def _listing(client, token, job_id):
    return client.get(f"/api/v1/jobs/{job_id}/interviews", headers=auth_header(token)).json()


def test_selected_candidates_are_listed_to_schedule(
    client: TestClient, interviewer_token: str, selected
):
    data = _listing(client, interviewer_token, selected["job_id"])
    assert data["channel"] == "smtp"
    assert [c["candidate_id"] for c in data["to_schedule"]] == [selected["candidate_id"]]
    assert data["interviews"] == []


def test_scheduling_with_google_books_the_event_and_sends_a_secure_link(
    client: TestClient,
    interviewer_token: str,
    selected,
    inline_jobs,
    runtime,
    google_connected,
    db_session,
):
    response = _schedule(client, interviewer_token, selected, inline_jobs, start=_tomorrow_at(15))
    assert response.status_code == 202, response.text
    assert inline_jobs[-1]["error"] is None, inline_jobs[-1]["error"]

    tools = runtime["made"][-1]
    assert tools.channel == "google"
    assert tools.names() == ["calendar_free_busy", "calendar_create_event", "gmail_send"]

    data = _listing(client, interviewer_token, selected["job_id"])
    assert data["channel"] == "google" and data["to_schedule"] == []
    [row] = data["interviews"]
    assert row["status"] == "scheduled"
    assert "meet_url" not in row and row["has_calendar_event"] is True
    assert row["invitation_channel"] == "gmail" and row["pending_action"] is None
    assert row["scheduled_start"].startswith(_tomorrow_at(15)[:10])

    # the link in the email works; only its hash is stored
    link = runtime["contact"].join_url
    token = urlparse(link).path.rsplit("/", 1)[-1]
    assert link in tools.sent()["body"]
    stored = db_session.scalar(select(Interview))
    assert stored.join_token != token and len(stored.join_token) == 64
    page = client.get(f"/api/v1/interviews/join/{token}")
    assert page.status_code == 200
    body = page.json()
    assert body["status"] == "scheduled"
    # nothing about the candidate or their scores on the public page
    assert set(body) == {
        "job_title",
        "status",
        "scheduled_start",
        "scheduled_end",
        "timezone_name",
        "interviewer_name",
    }
    assert client.get("/api/v1/interviews/join/not-a-real-token").status_code == 404

    run = db_session.scalar(select(AgentRun).where(AgentRun.graph_name == "interview_scheduling"))
    assert run.context["candidate_id"] == selected["candidate_id"]


def test_without_google_the_invite_goes_by_smtp(
    client: TestClient, interviewer_token: str, selected, inline_jobs, runtime
):
    _schedule(client, interviewer_token, selected, inline_jobs, start=_tomorrow_at(11))
    assert runtime["made"][-1].names() == ["smtp_send"]
    [row] = _listing(client, interviewer_token, selected["job_id"])["interviews"]
    assert row["invitation_channel"] == "smtp" and row["has_calendar_event"] is False
    assert "not checked" in row["last_error"]


def test_a_conflict_is_recorded_and_can_be_overridden(
    client: TestClient,
    interviewer_token: str,
    selected,
    inline_jobs,
    runtime,
    google_connected,
):
    start = _tomorrow_at(15)
    runtime["busy"] = [{"start": f"{start}:00+06:00", "end": f"{start[:11]}16:00:00+06:00"}]
    _schedule(client, interviewer_token, selected, inline_jobs, start=start)
    assert inline_jobs[-1]["error"] is not None
    [row] = _listing(client, interviewer_token, selected["job_id"])["interviews"]
    assert row["status"] == "failed" and row["last_outcome"] == "conflict"
    assert "busy" in row["last_error"]

    # trying again on the failed row is a fresh invitation
    response = client.post(
        f"/api/v1/interviews/{row['id']}/reschedule",
        json={"duration_minutes": 45, "time_zone": TZ, "start": start, "allow_conflict": True},
        headers=auth_header(interviewer_token),
    )
    assert response.status_code == 202
    assert inline_jobs[-1]["error"] is None, inline_jobs[-1]["error"]
    [row] = _listing(client, interviewer_token, selected["job_id"])["interviews"]
    assert row["status"] == "scheduled"
    assert runtime["kinds"][-1] == "invitation"
    assert runtime["made"][-1].names()[-1] == "gmail_send"


def test_reschedule_issues_a_new_link_and_cancel_closes_it(
    client: TestClient,
    interviewer_token: str,
    selected,
    inline_jobs,
    runtime,
    google_connected,
):
    headers = auth_header(interviewer_token)
    _schedule(client, interviewer_token, selected, inline_jobs, start=_tomorrow_at(15))
    first = urlparse(runtime["contact"].join_url).path.rsplit("/", 1)[-1]
    [row] = _listing(client, interviewer_token, selected["job_id"])["interviews"]

    client.post(
        f"/api/v1/interviews/{row['id']}/reschedule",
        json={"duration_minutes": 60, "time_zone": TZ, "start": _tomorrow_at(10)},
        headers=headers,
    )
    assert inline_jobs[-1]["error"] is None, inline_jobs[-1]["error"]
    assert runtime["made"][-1].names() == [
        "calendar_free_busy",
        "calendar_update_event",
        "gmail_send",
    ]
    second = urlparse(runtime["contact"].join_url).path.rsplit("/", 1)[-1]
    assert client.get(f"/api/v1/interviews/join/{first}").status_code == 404
    assert client.get(f"/api/v1/interviews/join/{second}").status_code == 200
    [row] = _listing(client, interviewer_token, selected["job_id"])["interviews"]
    assert row["duration_minutes"] == 60

    client.post(
        f"/api/v1/interviews/{row['id']}/cancel", json={"reason": "Role filled"}, headers=headers
    )
    assert inline_jobs[-1]["error"] is None, inline_jobs[-1]["error"]
    assert runtime["made"][-1].names() == ["gmail_send", "calendar_delete_event"]
    page = client.get(f"/api/v1/interviews/join/{second}").json()
    assert page["status"] == "cancelled"
    # cancelled, so the candidate can be scheduled again
    data = _listing(client, interviewer_token, selected["job_id"])
    assert [c["candidate_id"] for c in data["to_schedule"]] == [selected["candidate_id"]]


def test_requests_are_checked_before_anything_is_queued(
    client: TestClient, interviewer_token: str, selected, inline_jobs, runtime
):
    bad = [
        ({"time_zone": "Mars/Base"}, "time zone"),
        ({"duration_minutes": 50, "start": _tomorrow_at(15)}, "30, 45, 60 or 90"),
        ({}, "describe when"),
    ]
    for options, message in bad:
        response = _schedule(client, interviewer_token, selected, inline_jobs, **options)
        assert response.status_code == 422, options
        assert message in response.json()["error"]["message"]
    assert inline_jobs == [] or all(j["kind"] != "interview_scheduling" for j in inline_jobs)

    # not selected for interview
    response = client.post(
        f"/api/v1/jobs/{selected['job_id']}/interviews",
        json={"candidate_id": 999, "time_zone": TZ, "start": _tomorrow_at(15)},
        headers=auth_header(interviewer_token),
    )
    assert response.status_code == 422 and "Scoring step" in response.json()["error"]["message"]


def test_one_interview_at_a_time_and_no_deletion_while_booked(
    client: TestClient, interviewer_token: str, selected, inline_jobs, runtime
):
    _schedule(client, interviewer_token, selected, inline_jobs, start=_tomorrow_at(15))
    again = _schedule(client, interviewer_token, selected, inline_jobs, start=_tomorrow_at(16))
    assert again.status_code == 409
    deleted = client.delete(
        f"/api/v1/candidates/{selected['candidate_id']}", headers=auth_header(interviewer_token)
    )
    assert deleted.status_code == 409 and "Cancel" in deleted.json()["error"]["message"]


def test_interviews_are_closed_to_candidates(client: TestClient, candidate_token: str, selected):
    headers = auth_header(candidate_token)
    assert (
        client.get(f"/api/v1/jobs/{selected['job_id']}/interviews", headers=headers).status_code
        == 403
    )
    response = client.post(
        f"/api/v1/jobs/{selected['job_id']}/interviews",
        json={"candidate_id": selected["candidate_id"], "time_zone": TZ, "start": _tomorrow_at(15)},
        headers=headers,
    )
    assert response.status_code == 403
    assert client.post("/api/v1/interviews/1/cancel", json={}, headers=headers).status_code == 403
