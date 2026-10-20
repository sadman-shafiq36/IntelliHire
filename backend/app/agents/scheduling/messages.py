# the invitation, reschedule and cancellation emails. code owns the layout (greeting,
# interview details, calendar, signature); the ai writes only the subject, a short opening
# and a closing, and never sees the candidate's name, the time or the link
#
# interviews are held in intellihire's own video room, never google meet

import html
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

from icalendar import Calendar, Event, vCalAddress, vText

from app.agents.jd.review import biased_terms_in

MAX_SUBJECT = 150
MAX_OPENING = 600
MAX_CLOSING = 400
ROOM = "IntelliHire video room"

GREETING = re.compile(r"^\s*(dear|hi|hello|hey|good (morning|afternoon|evening))\b", re.I)
SIGN_OFF = re.compile(r"\b(regards|sincerely|best wishes|yours (truly|faithfully))\b", re.I)
URL = re.compile(r"https?://|www\.", re.I)
# a time, a date or a weekday would compete with the details block, which is the truth
WHEN = re.compile(
    r"\b(\d{1,2}[:.]\d{2}|\d{1,2}\s?(am|pm)|monday|tuesday|wednesday|thursday|friday|"
    r"saturday|sunday|tomorrow|today|\d{1,2}(st|nd|rd|th)?\s+(jan|feb|mar|apr|may|jun|jul|"
    r"aug|sep|oct|nov|dec))",
    re.I,
)

TEMPLATES = {
    "invitation": {
        "subject": "Interview invitation: {job_title}",
        "opening": "Thank you for applying for the {job_title} role. We would like to invite "
        "you to an interview.",
        "closing": "If the time does not suit you, please reply to this email and we will find "
        "another. We look forward to speaking with you.",
    },
    "reschedule": {
        "subject": "New time for your {job_title} interview",
        "opening": "Your interview for the {job_title} role has moved to a new time. Your "
        "personal interview link has also changed, so please use the one below.",
        "closing": "If the new time does not suit you, please reply to this email.",
    },
    "cancellation": {
        "subject": "Your {job_title} interview has been cancelled",
        "opening": "We are sorry to let you know that your interview for the {job_title} role "
        "has been cancelled.",
        "closing": "Thank you for your time and your interest in the role.",
    },
}


@dataclass(frozen=True)
class Signature:
    name: str
    title: str
    company: str

    def lines(self) -> list[str]:
        return [part for part in (self.name, self.title, self.company) if part]


def check_draft(draft: dict[str, Any], kind: str) -> list[str]:
    # problems phrased as fixes for the redraft
    problems: list[str] = []
    subject = (draft.get("subject") or "").strip()
    opening = (draft.get("opening") or "").strip()
    closing = (draft.get("closing") or "").strip()
    if not subject or len(subject) > MAX_SUBJECT:
        problems.append(f"The subject must be 1 to {MAX_SUBJECT} characters.")
    if not opening or len(opening) > MAX_OPENING:
        problems.append(f"The opening must be 1 to {MAX_OPENING} characters.")
    if len(closing) > MAX_CLOSING:
        problems.append(f"The closing must be at most {MAX_CLOSING} characters.")
    # found live: "Update regarding your interview" hid a cancellation from the inbox
    if kind == "cancellation" and "cancel" not in subject.lower():
        problems.append('The subject must say the interview is cancelled, e.g. "... cancelled".')
    if GREETING.search(opening):
        problems.append("Start the opening without a greeting; the greeting is added for you.")
    if SIGN_OFF.search(opening + " " + closing):
        problems.append("Leave out the sign-off and name; the signature is added for you.")
    text = " ".join([subject, opening, closing])
    if URL.search(text):
        problems.append("Leave out links; the interview link is added for you.")
    if WHEN.search(text):
        problems.append("Leave out dates, days and times; the interview details are added for you.")
    if "{" in text or "}" in text:
        problems.append("Write plain sentences with no braces or placeholders.")
    biased = biased_terms_in([subject, opening, closing])
    if biased:
        problems.append(
            f"Remove wording that could be discriminatory: {', '.join(sorted(biased))}."
        )
    return problems


def template(kind: str, job_title: str) -> dict[str, str]:
    return {k: v.replace("{job_title}", job_title) for k, v in TEMPLATES[kind].items()}


def describe_date(start: datetime) -> str:
    # e.g. "Tuesday 20 October 2026"
    return f"{start.strftime('%A')} {start.day} {start.strftime('%B %Y')}"


def describe_time(start: datetime, end: datetime, tz_name: str) -> str:
    return f"{start.strftime('%H:%M')} to {end.strftime('%H:%M')} ({tz_name})"


def describe_when(start: datetime, tz_name: str) -> str:
    return f"{describe_date(start)} at {start.strftime('%H:%M')} ({tz_name})"


def describe_duration(minutes: int) -> str:
    if minutes % 60:
        return f"{minutes} minutes"
    hours = minutes // 60
    return f"{hours} hour" + ("s" if hours > 1 else "")


def google_calendar_link(
    title: str, start: datetime, end: datetime, details: str, location: str
) -> str:
    # opens google calendar with the event filled in, one click to save it
    fmt = "%Y%m%dT%H%M%SZ"
    query = urlencode(
        {
            "action": "TEMPLATE",
            "text": title,
            "dates": f"{start.astimezone(UTC).strftime(fmt)}/{end.astimezone(UTC).strftime(fmt)}",
            "details": details,
            "location": location,
        }
    )
    return f"https://calendar.google.com/calendar/render?{query}"


@dataclass(frozen=True)
class Email:
    subject: str
    text: str
    html: str


def compose(
    kind: str,
    draft: dict[str, str],
    candidate_name: str,
    job_title: str,
    start: datetime,
    end: datetime,
    tz_name: str,
    join_url: str | None,
    signature: Signature,
) -> Email:
    opening = draft["opening"].strip()
    closing = (draft.get("closing") or "").strip()
    minutes = int((end - start).total_seconds() // 60)
    details = [
        ("Role", job_title),
        ("Date", describe_date(start)),
        ("Time", describe_time(start, end, tz_name)),
        ("Length", describe_duration(minutes)),
    ]
    cancelled = kind == "cancellation"
    if not cancelled:
        details.append(("Where", f"{ROOM}, online"))
    add_link = (
        google_calendar_link(
            f"Interview: {job_title}",
            start,
            end,
            f"Join the interview: {join_url}",
            join_url or ROOM,
        )
        if not cancelled and join_url
        else None
    )
    sign = signature.lines()

    # plain text: short blocks with blank lines between them
    text_parts = [f"Dear {candidate_name},", opening]
    detail_lines = ["Interview details"] + [f"{k}: {v}" for k, v in details]
    text_parts.append("\n".join(detail_lines))
    if not cancelled and join_url:
        text_parts.append(f"Join the interview from your personal link:\n{join_url}")
        text_parts.append(
            "Add it to your calendar: open the attached invitation, or use this link:\n"
            + (add_link or "")
        )
    elif cancelled:
        text_parts.append("The attached calendar update removes the interview from your calendar.")
    if closing:
        text_parts.append(closing)
    text_parts.append("\n".join(["Best regards,", *sign]))
    text = "\n\n".join(text_parts) + "\n"

    e = html.escape
    rows = "".join(
        f'<tr><td style="padding:4px 16px 4px 0;color:#61685c;white-space:nowrap">{e(k)}</td>'
        f'<td style="padding:4px 0;color:#17211f">{e(v)}</td></tr>'
        for k, v in details
    )
    actions = ""
    if not cancelled and join_url:
        actions = (
            f'<p style="margin:24px 0 8px"><a href="{e(join_url)}" style="display:inline-block;'
            "background:#126e5a;color:#ffffff;text-decoration:none;padding:10px 18px;"
            'border-radius:6px;font-weight:600">Join the interview</a></p>'
            f'<p style="margin:0 0 4px;font-size:13px;color:#61685c">Or copy this link: '
            f'<a href="{e(join_url)}" style="color:#126e5a">{e(join_url)}</a></p>'
            f'<p style="margin:16px 0 0;font-size:14px"><a href="{e(add_link or "")}" '
            'style="color:#126e5a;font-weight:600">Add to Google Calendar</a>'
            '<span style="color:#61685c"> · or open the attached invitation (invite.ics) '
            "to add it to any calendar</span></p>"
        )
    elif cancelled:
        actions = (
            '<p style="margin:16px 0 0;font-size:14px;color:#61685c">The attached calendar '
            "update removes the interview from your calendar.</p>"
        )
    signature_html = "<br>".join(e(line) for line in ["Best regards,", *sign])
    body_html = (
        '<div style="font-family:Arial,Helvetica,sans-serif;font-size:15px;line-height:1.55;'
        'color:#17211f;max-width:560px">'
        f'<p style="margin:0 0 16px">Dear {e(candidate_name)},</p>'
        f'<p style="margin:0 0 20px">{e(opening)}</p>'
        '<div style="border:1px solid #d9dcd2;border-radius:8px;padding:16px 20px;'
        'background:#f4f5f1">'
        '<p style="margin:0 0 8px;font-weight:600">Interview details</p>'
        f'<table style="border-collapse:collapse;font-size:14px">{rows}</table></div>'
        f"{actions}"
        + (f'<p style="margin:24px 0 0">{e(closing)}</p>' if closing else "")
        + f'<p style="margin:24px 0 0">{signature_html}</p></div>'
    )
    return Email(subject=draft["subject"].strip(), text=text, html=body_html)


def new_uid() -> str:
    return f"{uuid.uuid4().hex}@intellihire.local"


def build_ics(
    uid: str,
    sequence: int,
    method: str,
    summary: str,
    description: str,
    start: datetime,
    end: datetime,
    organizer_email: str | None,
    organizer_name: str | None,
    attendee_email: str,
    attendee_name: str,
    location: str,
) -> str:
    cal = Calendar()
    cal.add("prodid", "-//IntelliHire//Interview scheduling//EN")
    cal.add("version", "2.0")
    cal.add("method", method)
    event = Event()
    event.add("uid", uid)
    # a higher sequence tells calendars this replaces the earlier version
    event.add("sequence", sequence)
    # in utc: a fixed offset like +06:00 has no tz id calendars recognise, and without
    # one some apps misread the time
    event.add("dtstamp", datetime.now(UTC))
    event.add("dtstart", start.astimezone(UTC))
    event.add("dtend", end.astimezone(UTC))
    event.add("summary", summary)
    event.add("description", description)
    event.add("location", location)
    if organizer_email:
        organizer = vCalAddress(f"mailto:{organizer_email}")
        if organizer_name:
            organizer.params["cn"] = vText(organizer_name)
        event["organizer"] = organizer
    attendee = vCalAddress(f"mailto:{attendee_email}")
    attendee.params["cn"] = vText(attendee_name)
    attendee.params["role"] = vText("REQ-PARTICIPANT")
    attendee.params["partstat"] = vText("NEEDS-ACTION")
    attendee.params["rsvp"] = vText("TRUE")
    event.add("attendee", attendee, encode=0)
    event.add("status", "CANCELLED" if method == "CANCEL" else "CONFIRMED")
    cal.add_component(event)
    return cal.to_ical().decode()
