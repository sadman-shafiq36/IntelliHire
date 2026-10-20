# every google calendar and gmail call the mcp server makes. the access token comes
# with each request from the backend; this service never stores one

import base64
from typing import Any

import httpx

from settings import settings

CALENDAR = "https://www.googleapis.com/calendar/v3"
GMAIL = "https://gmail.googleapis.com/gmail/v1"

_transport: httpx.BaseTransport | None = None


def set_transport(transport: httpx.BaseTransport | None) -> None:
    global _transport
    _transport = transport


class GoogleError(Exception):
    # message is safe to show to an interviewer; the response body never is
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def _client(token: str) -> httpx.Client:
    return httpx.Client(
        timeout=settings.http_timeout_seconds,
        transport=_transport,
        headers={"Authorization": f"Bearer {token}"},
    )


def _check(response: httpx.Response, action: str) -> dict[str, Any]:
    if response.is_success:
        return response.json() if response.content else {}
    status = response.status_code
    if status in (401, 403):
        message = (
            "Google refused the request. Reconnect Google in Settings → Integrations and "
            "allow Gmail and Calendar access."
        )
    elif status == 404:
        message = f"Google could not find what we asked for ({action})."
    elif status == 429:
        message = "Google is rate limiting us. Please try again in a few minutes."
    else:
        message = f"Google could not complete the request ({action}). Please try again."
    raise GoogleError(message, status)


def free_busy(token: str, time_min: str, time_max: str, time_zone: str) -> list[dict[str, str]]:
    with _client(token) as c:
        body = _check(
            c.post(
                f"{CALENDAR}/freeBusy",
                json={
                    "timeMin": time_min,
                    "timeMax": time_max,
                    "timeZone": time_zone,
                    "items": [{"id": "primary"}],
                },
            ),
            "check availability",
        )
    calendar = (body.get("calendars") or {}).get("primary") or {}
    if calendar.get("errors"):
        raise GoogleError("Google could not read the calendar's availability.")
    return [{"start": b["start"], "end": b["end"]} for b in calendar.get("busy", [])]


def _event_body(
    summary: str,
    description: str,
    location: str,
    start: str,
    end: str,
    time_zone: str,
    attendees: list[str],
) -> dict[str, Any]:
    # no conferenceData: interviews are held in intellihire's own room, never google meet
    return {
        "summary": summary,
        "description": description,
        "location": location,
        "start": {"dateTime": start, "timeZone": time_zone},
        "end": {"dateTime": end, "timeZone": time_zone},
        "attendees": [{"email": a} for a in attendees],
        # our own email is the invitation; the event is for the interviewer's calendar
        "guestsCanSeeOtherGuests": False,
        "reminders": {"useDefault": True},
    }


def _summary(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "event_id": event.get("id"),
        "html_link": event.get("htmlLink"),
        "start": (event.get("start") or {}).get("dateTime"),
        "end": (event.get("end") or {}).get("dateTime"),
    }


def create_event(
    token: str,
    summary: str,
    description: str,
    location: str,
    start: str,
    end: str,
    time_zone: str,
    attendees: list[str],
) -> dict[str, Any]:
    body = _event_body(summary, description, location, start, end, time_zone, attendees)
    with _client(token) as c:
        event = _check(
            c.post(
                f"{CALENDAR}/calendars/primary/events",
                params={"sendUpdates": "none"},
                json=body,
            ),
            "create the calendar event",
        )
    return _summary(event)


def update_event(
    token: str, event_id: str, start: str, end: str, time_zone: str, description: str | None
) -> dict[str, Any]:
    patch: dict[str, Any] = {
        "start": {"dateTime": start, "timeZone": time_zone},
        "end": {"dateTime": end, "timeZone": time_zone},
    }
    if description is not None:
        patch["description"] = description
    with _client(token) as c:
        event = _check(
            c.patch(
                f"{CALENDAR}/calendars/primary/events/{event_id}",
                params={"sendUpdates": "none"},
                json=patch,
            ),
            "move the calendar event",
        )
    return _summary(event)


def delete_event(token: str, event_id: str) -> None:
    with _client(token) as c:
        response = c.delete(
            f"{CALENDAR}/calendars/primary/events/{event_id}", params={"sendUpdates": "none"}
        )
    # already gone is the outcome we wanted
    if response.status_code in (404, 410):
        return
    _check(response, "delete the calendar event")


def send_message(token: str, raw_mime: bytes) -> str:
    with _client(token) as c:
        body = _check(
            c.post(
                f"{GMAIL}/users/me/messages/send",
                json={"raw": base64.urlsafe_b64encode(raw_mime).decode()},
            ),
            "send the email",
        )
    return body.get("id", "")
