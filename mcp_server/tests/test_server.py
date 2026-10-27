# runs the real server on a local port and calls it with the official mcp client, the
# same way the worker does. google is an in-memory fake; smtp is a stub

import asyncio
import base64
import email
import json
import logging
import socket
import threading
import time

import httpx
import pytest
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

import google_api
import mailer
import server

SECRET = "test-secret"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def base_url():
    port = _free_port()
    object.__setattr__(server.settings, "shared_secret", SECRET)
    # the host check must accept the test server's own address
    server.mcp.settings.transport_security.allowed_hosts.append(f"127.0.0.1:{port}")
    config = uvicorn.Config(server.app, host="127.0.0.1", port=port, log_level="warning")
    srv = uvicorn.Server(config)
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    thread.join(timeout=5)


class FakeGoogle:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.fail_with: int | None = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.fail_with:
            return httpx.Response(self.fail_with, json={"error": {"message": "token=abc secret"}})
        path = request.url.path
        if path.endswith("/freeBusy"):
            return httpx.Response(
                200,
                json={
                    "calendars": {
                        "primary": {
                            "busy": [
                                {"start": "2026-10-20T09:00:00Z", "end": "2026-10-20T10:00:00Z"}
                            ]
                        }
                    }
                },
            )
        if path.endswith("/events") and request.method == "POST":
            body = json.loads(request.content)
            return httpx.Response(
                200,
                json={
                    "id": "evt-1",
                    "htmlLink": "https://calendar.google.com/event?eid=1",
                    "start": body["start"],
                    "end": body["end"],
                },
            )
        if "/events/" in path and request.method == "PATCH":
            body = json.loads(request.content)
            return httpx.Response(
                200,
                json={"id": "evt-1", **body},
            )
        if "/events/" in path and request.method == "DELETE":
            return httpx.Response(404 if path.endswith("gone") else 204)
        if path.endswith("/messages/send"):
            return httpx.Response(200, json={"id": "msg-1"})
        return httpx.Response(500)


@pytest.fixture
def google():
    fake = FakeGoogle()
    google_api.set_transport(httpx.MockTransport(fake.handler))
    yield fake
    google_api.set_transport(None)


def call(base_url, tool, args, token="g-token", secret=SECRET):
    headers = {"x-mcp-key": secret}
    if token:
        headers["x-google-access-token"] = token

    async def run():
        async with (
            httpx.AsyncClient(headers=headers, timeout=30) as http,
            streamable_http_client(f"{base_url}/mcp", http_client=http) as (read, write, _),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            if tool is None:
                return await session.list_tools()
            return await session.call_tool(tool, args)

    return asyncio.run(run())


def result_json(result) -> dict:
    assert not result.isError, result.content[0].text
    return json.loads(result.content[0].text)


def test_lists_the_scheduling_tools(base_url):
    names = {t.name for t in call(base_url, None, None).tools}
    assert names == {
        "calendar_free_busy",
        "calendar_create_event",
        "calendar_update_event",
        "calendar_delete_event",
        "gmail_send",
        "smtp_send",
    }


def test_health_is_open_but_tools_need_the_secret(base_url):
    assert httpx.get(f"{base_url}/health").json()["tools"] == 6
    refused = httpx.post(f"{base_url}/mcp", json={}, headers={"x-mcp-key": "wrong"})
    assert refused.status_code == 401
    assert httpx.post(f"{base_url}/mcp", json={}).status_code == 401


def test_free_busy_passes_the_token_to_google(base_url, google):
    body = result_json(
        call(
            base_url,
            "calendar_free_busy",
            {
                "time_min": "2026-10-20T00:00:00Z",
                "time_max": "2026-10-21T00:00:00Z",
                "time_zone": "Asia/Dhaka",
            },
        )
    )
    assert body["busy"] == [{"start": "2026-10-20T09:00:00Z", "end": "2026-10-20T10:00:00Z"}]
    assert google.requests[0].headers["Authorization"] == "Bearer g-token"


def test_create_event_has_no_video_conference_and_no_google_invite(base_url, google):
    body = result_json(
        call(
            base_url,
            "calendar_create_event",
            {
                "summary": "Interview",
                "description": "Backend Engineer",
                "location": "IntelliHire video room",
                "start": "2026-10-20T15:00:00",
                "end": "2026-10-20T15:45:00",
                "time_zone": "Asia/Dhaka",
                "attendees": ["ada@example.com"],
            },
        )
    )
    assert body["event_id"] == "evt-1" and "meet_url" not in body
    request = google.requests[0]
    assert request.url.params["sendUpdates"] == "none"
    assert "conferenceDataVersion" not in request.url.params
    sent = json.loads(request.content)
    # interviews are held in intellihire, never google meet
    assert "conferenceData" not in sent
    assert sent["location"] == "IntelliHire video room"


def test_update_and_delete_events(base_url, google):
    moved = result_json(
        call(
            base_url,
            "calendar_update_event",
            {
                "event_id": "evt-1",
                "start": "2026-10-21T15:00:00",
                "end": "2026-10-21T15:45:00",
                "time_zone": "Asia/Dhaka",
            },
        )
    )
    assert moved["start"] == "2026-10-21T15:00:00"
    assert result_json(call(base_url, "calendar_delete_event", {"event_id": "evt-1"}))["deleted"]
    # an event someone already removed counts as deleted
    assert result_json(call(base_url, "calendar_delete_event", {"event_id": "gone"}))["deleted"]


def test_gmail_send_is_an_invite_mail_apps_can_add_to_a_calendar(base_url, google):
    body = result_json(
        call(
            base_url,
            "gmail_send",
            {
                "to": "ada@example.com",
                "subject": "Interview invitation",
                "body": "Hello Ada",
                "html": "<p>Hello Ada</p>",
                "ics": "BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n",
            },
        )
    )
    assert body == {"message_id": "msg-1", "channel": "gmail"}
    raw = json.loads(google.requests[0].content)["raw"]
    msg = email.message_from_bytes(base64.urlsafe_b64decode(raw))
    assert msg["To"] == "ada@example.com" and msg["Subject"] == "Interview invitation"
    # the layout calendar apps use: text, html and the invitation as alternatives, plus a file
    assert msg.get_content_type() == "multipart/mixed"
    alternative, attachment = msg.get_payload()
    assert [p.get_content_type() for p in alternative.get_payload()] == [
        "text/plain",
        "text/html",
        "text/calendar",
    ]
    assert alternative.get_payload()[2].get_param("method") == "REQUEST"
    assert attachment.get_filename() == "invite.ics"
    assert msg["From"] is None


def test_gmail_send_sets_the_sender_name(base_url, google):
    call(
        base_url,
        "gmail_send",
        {
            "to": "ada@example.com",
            "subject": "Hello",
            "body": "Hello Ada",
            "from_address": "AJIT Ltd <hiring@example.com>",
        },
    )
    raw = json.loads(google.requests[-1].content)["raw"]
    msg = email.message_from_bytes(base64.urlsafe_b64decode(raw))
    assert msg["From"] == "AJIT Ltd <hiring@example.com>"


def test_smtp_send_needs_no_google(base_url, monkeypatch):
    sent = []

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            self.host = host

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def send_message(self, msg):
            sent.append(msg)

    monkeypatch.setattr(mailer.smtplib, "SMTP", FakeSMTP)
    body = result_json(
        call(
            base_url,
            "smtp_send",
            {"to": "ada@example.com", "subject": "Cancelled", "body": "Sorry",
             "ics": "BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n", "ics_method": "CANCEL"},
            token=None,
        )
    )  # fmt: skip
    assert body["channel"] == "smtp"
    assert sent[0]["From"].endswith("<no-reply@intellihire.local>")
    cal = [p for p in sent[0].walk() if p.get_content_type() == "text/calendar"]
    assert cal[0].get_param("method") == "CANCEL"


def test_google_refusal_says_reconnect_and_hides_the_body(base_url, google, caplog):
    google.fail_with = 401
    with caplog.at_level(logging.INFO):
        result = call(
            base_url,
            "gmail_send",
            {"to": "ada@example.com", "subject": "Hi", "body": "Hello"},
        )
    assert result.isError
    text = result.content[0].text
    assert "Reconnect Google" in text and "secret" not in text
    # the address never reaches the logs
    assert "ada@example.com" not in caplog.text


def test_google_tools_need_a_token(base_url, google):
    result = call(base_url, "calendar_delete_event", {"event_id": "evt-1"}, token=None)
    assert result.isError and "not connected" in result.content[0].text
    assert google.requests == []
