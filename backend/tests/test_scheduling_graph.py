from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.agents.scheduling import messages, slots
from app.agents.scheduling.graph import Contact, build_scheduling_graph
from app.errors import ExternalServiceError

TZ = "Asia/Dhaka"
NOW = "2026-10-19T10:00:00+06:00"  # a monday
SIGNATURE = messages.Signature("Sadman Shafiq", "Manager", "AJIT Ltd")
CONTACT = Contact("Ada Lovelace", "ada@example.com", "http://localhost:5173/interview/tok123")


class FakeTools:
    def __init__(self, channel="google", busy=(), fail=()):
        self.channel = channel
        self.busy = list(busy)
        self.fail = set(fail)
        self.calls: list[tuple[str, dict]] = []

    def call(self, name, arguments):
        self.calls.append((name, arguments))
        if name in self.fail:
            raise ExternalServiceError(f"{name} failed for test")
        if name == "calendar_free_busy":
            return {"busy": self.busy}
        if name == "calendar_create_event":
            return {"event_id": "evt-1"}
        if name == "calendar_update_event":
            return {"event_id": arguments["event_id"]}
        if name == "calendar_delete_event":
            return {"deleted": True}
        return {"message_id": "msg-1", "channel": "gmail" if "gmail" in name else "smtp"}

    def names(self):
        return [n for n, _ in self.calls]

    def sent(self):
        return next(a for n, a in self.calls if n.endswith("_send"))


def good_drafter(payload):
    if payload["kind"] == "cancellation":
        return {
            "subject": f"Your {payload['job_title']} interview has been cancelled",
            "opening": "We are sorry to tell you the interview will not go ahead.",
            "closing": "Thank you for your interest in the role.",
        }, "fake-model"
    return {
        "subject": f"Interview invitation: {payload['job_title']}",
        "opening": "Thank you for applying. We would like to invite you to an interview.",
        "closing": "We look forward to speaking with you.",
    }, "fake-model"


def interpreter_returning(*answers):
    calls = []

    def interpreter(payload):
        calls.append(payload)
        return answers[min(len(calls), len(answers)) - 1], "fake-model"

    interpreter.calls = calls
    return interpreter


def _state(**overrides):
    state = {
        "thread_id": "t",
        "action": "schedule",
        "job_title": "Backend Engineer",
        "interviewer_name": "Sam Interviewer",
        "organizer_email": "sam@example.com",
        "interviewer_url": "http://localhost:5173/jobs/1?tab=interviews",
        "time_zone": TZ,
        "duration_minutes": 45,
        "now": NOW,
        "start": None,
        "preference": None,
        "allow_conflict": False,
        "note": None,
        "reason": None,
        "existing": None,
        "errors": [],
        "warnings": [],
    }
    return state | overrides


def run(tools, state, drafter=good_drafter, interpreter=None):
    graph = build_scheduling_graph(
        tools,
        CONTACT,
        drafter=drafter,
        interpreter=interpreter or interpreter_returning({}),
        signature=SIGNATURE,
    )
    return graph.invoke(state)


def test_scheduling_books_the_calendar_and_sends_one_structured_gmail():
    tools = FakeTools()
    final = run(tools, _state(start="2026-10-20T15:00"))

    assert final["outcome"] == "scheduled"
    assert tools.names() == ["calendar_free_busy", "calendar_create_event", "gmail_send"]
    event = tools.calls[1][1]
    assert event["attendees"] == ["ada@example.com"]
    assert event["start"] == "2026-10-20T15:00:00+06:00"
    # held in intellihire, never google meet
    assert "with_meet" not in event and event["location"] == "IntelliHire video room"
    assert "meet.google" not in repr(tools.calls)

    mail = tools.sent()
    assert mail["to"] == "ada@example.com"
    text = mail["body"]
    # code lays it out: greeting, opening, details, link, calendar, closing, signature,
    # each block separated by a blank line
    blocks = text.strip().split("\n\n")
    assert blocks[0] == "Dear Ada Lovelace,"
    assert blocks[1].startswith("Thank you for applying")
    assert blocks[2].splitlines() == [
        "Interview details",
        "Role: Backend Engineer",
        "Date: Tuesday 20 October 2026",
        "Time: 15:00 to 15:45 (Asia/Dhaka)",
        "Length: 45 minutes",
        "Where: IntelliHire video room, online",
    ]
    assert "http://localhost:5173/interview/tok123" in blocks[3]
    assert "calendar.google.com/calendar/render?action=TEMPLATE" in blocks[4]
    assert blocks[-1].splitlines() == ["Best regards,", "Sadman Shafiq", "Manager", "AJIT Ltd"]
    assert "Join the interview" in mail["html"] and "Add to Google Calendar" in mail["html"]
    assert "Sadman Shafiq<br>Manager<br>AJIT Ltd" in mail["html"]
    # 15:00 in Dhaka, written in utc so every calendar app reads it the same
    assert mail["ics_method"] == "REQUEST" and "DTSTART:20261020T090000Z" in mail["ics"]
    # long ics lines are folded at 75 characters; calendar apps unfold them
    ics = mail["ics"].replace("\r\n ", "")
    assert "ATTENDEE;" in ics and "RSVP=TRUE" in ics and 'CN="Ada Lovelace"' in ics


def test_the_ai_never_sees_the_candidate():
    seen = []

    def drafter(payload):
        seen.append(repr(payload))
        return good_drafter(payload)

    interpreter = interpreter_returning(
        {"windows": [{"start": "2026-10-20T13:00", "end": "2026-10-20T17:00"}], "understood": "x"}
    )
    run(FakeTools(), _state(preference="tomorrow afternoon"), drafter, interpreter)
    seen.append(repr(interpreter.calls))
    text = " ".join(seen)
    assert "Ada" not in text and "ada@example.com" not in text and "tok123" not in text


def test_a_busy_chosen_time_is_a_conflict_unless_overridden():
    busy = [{"start": "2026-10-20T15:30:00+06:00", "end": "2026-10-20T16:00:00+06:00"}]
    tools = FakeTools(busy=busy)
    final = run(tools, _state(start="2026-10-20T15:00"))
    assert final["outcome"] == "conflict"
    assert final["busy"][0]["start"].startswith("2026-10-20T15:30")
    assert tools.names() == ["calendar_free_busy"]

    tools = FakeTools(busy=busy)
    final = run(tools, _state(start="2026-10-20T15:00", allow_conflict=True))
    assert final["outcome"] == "scheduled"


def test_the_agent_finds_the_first_free_slot_from_a_description():
    busy = [{"start": "2026-10-20T13:00:00+06:00", "end": "2026-10-20T14:30:00+06:00"}]
    interpreter = interpreter_returning(
        {"windows": [{"start": "2026-10-20T13:00", "end": "2026-10-20T17:00"}],
         "understood": "Tuesday afternoon"}
    )  # fmt: skip
    tools = FakeTools(busy=busy)
    final = run(tools, _state(preference="tuesday afternoon"), interpreter=interpreter)
    assert final["slot_start"] == "2026-10-20T14:30:00+06:00"
    assert final["understood"] == "Tuesday afternoon"


def test_unusable_windows_are_sent_back_once_then_the_user_is_asked_for_a_time():
    interpreter = interpreter_returning({"windows": [{"start": "2020-01-01T09:00", "end": "x"}]})
    final = run(FakeTools(), _state(preference="whenever"), interpreter=interpreter)
    assert len(interpreter.calls) == 2
    assert interpreter.calls[1]["feedback"]
    assert final["outcome"] == "failed" and "exact time" in final["message"]


def test_no_free_slot_is_reported():
    busy = [{"start": "2026-10-20T00:00:00+06:00", "end": "2026-10-21T00:00:00+06:00"}]
    interpreter = interpreter_returning(
        {"windows": [{"start": "2026-10-20T13:00", "end": "2026-10-20T17:00"}]}
    )
    final = run(FakeTools(busy=busy), _state(preference="tue pm"), interpreter=interpreter)
    assert final["outcome"] == "no_slot"


def test_a_failed_email_removes_the_event_it_created():
    tools = FakeTools(fail={"gmail_send"})
    final = run(tools, _state(start="2026-10-20T15:00"))
    assert final["outcome"] == "failed"
    assert "gmail_send failed" in final["message"]
    assert tools.names()[-1] == "calendar_delete_event"


def test_a_draft_breaking_the_rules_is_redrafted_then_replaced_by_the_template():
    calls = []

    def bad(payload):
        calls.append(payload)
        return {
            "subject": "Hi",
            "opening": "Dear Ada, see you on Tuesday at 3pm on https://meet.google.com/x!",
            "closing": "Best regards, Sam",
        }, "m"

    tools = FakeTools()
    final = run(tools, _state(start="2026-10-20T15:00"), drafter=bad)
    assert len(calls) == 2
    feedback = " ".join(calls[1]["feedback"])
    assert "greeting" in feedback and "links" in feedback and "times" in feedback
    assert "sign-off" in feedback
    assert final["used_template"] is True
    body = tools.sent()["body"]
    assert "http://localhost:5173/interview/tok123" in body and "meet.google" not in body


def test_without_google_it_uses_smtp_and_skips_the_calendar():
    tools = FakeTools(channel="smtp")
    final = run(tools, _state(start="2026-10-20T15:00"))
    assert final["outcome"] == "scheduled"
    assert tools.names() == ["smtp_send"]
    assert any("not checked" in w for w in final["warnings"])
    assert "meet.google" not in tools.sent()["body"]


def test_reschedule_moves_the_event_and_bumps_the_calendar_sequence():
    existing = {
        "event_id": "evt-1",
        "start": "2026-10-20T15:00:00+06:00",
        "end": "2026-10-20T15:45:00+06:00",
        "ics_uid": "uid-1@intellihire.local",
        "ics_sequence": 0,
    }
    # its own current slot shows as busy and must not count as a clash
    busy = [{"start": existing["start"], "end": existing["end"]}]
    tools = FakeTools(busy=busy)
    final = run(tools, _state(action="reschedule", start="2026-10-20T15:15", existing=existing))
    assert final["outcome"] == "rescheduled"
    assert tools.names() == ["calendar_free_busy", "calendar_update_event", "gmail_send"]
    assert final["ics_uid"] == "uid-1@intellihire.local" and final["ics_sequence"] == 1
    assert "SEQUENCE:1" in tools.sent()["ics"]


def test_cancel_tells_the_candidate_before_removing_the_event():
    # as the database returns it: in utc
    existing = {
        "event_id": "evt-1",
        "start": "2026-10-20T09:00:00+00:00",
        "end": "2026-10-20T09:45:00+00:00",
        "ics_uid": "uid-1@intellihire.local",
        "ics_sequence": 1,
    }
    tools = FakeTools()
    final = run(tools, _state(action="cancel", existing=existing, reason="Role filled"))
    assert final["outcome"] == "cancelled"
    assert tools.names() == ["gmail_send", "calendar_delete_event"]
    # found live: the email said 09:00 (Asia/Dhaka) for a 15:00 interview
    assert "Time: 15:00 to 15:45 (Asia/Dhaka)" in tools.sent()["body"]
    assert tools.sent()["ics_method"] == "CANCEL"
    assert final.get("used_template") is not True
    assert "STATUS:CANCELLED" in tools.sent()["ics"]

    # if the email fails, the event stays
    tools = FakeTools(fail={"gmail_send"})
    final = run(tools, _state(action="cancel", existing=existing))
    assert final["outcome"] == "failed" and tools.names() == ["gmail_send"]


def test_a_time_in_the_past_is_refused():
    final = run(FakeTools(), _state(start="2026-10-18T15:00"))
    assert final["outcome"] == "failed" and "future" in final["message"]


def test_first_free_slot_keeps_to_working_hours_and_weekdays():
    tz = ZoneInfo(TZ)
    friday_evening = datetime(2026, 10, 23, 16, 30, tzinfo=tz)
    windows = [(friday_evening, friday_evening + timedelta(days=4))]
    start = slots.first_free_slot(
        windows, [], timedelta(minutes=45), datetime(2026, 10, 19, 10, tzinfo=tz), 9, 17
    )
    # 16:30 + 45 min runs past 17:00, then the weekend, so monday 09:00
    assert start == datetime(2026, 10, 26, 9, 0, tzinfo=tz)


@pytest.mark.parametrize(
    "draft,problem",
    [
        ({"opening": "Hello! We would like to meet you."}, "greeting"),
        ({"closing": "Kind regards, Sam"}, "sign-off"),
        ({"opening": "Join at https://example.com"}, "links"),
        ({"opening": "See you on Monday at 10:00."}, "times"),
        ({"opening": "We need young energetic people."}, "discriminatory"),
        ({"opening": "Hi {candidate_name}"}, "braces"),
    ],
)
def test_draft_checks(draft, problem):
    full = {"subject": "Interview invitation", "opening": "We would like to meet you."} | draft
    problems = messages.check_draft(full, "invitation")
    assert any(problem in p for p in problems), problems


def test_a_cancellation_says_so_in_the_subject():
    opening = "We are sorry to tell you the interview will not go ahead."
    vague = messages.check_draft(
        {"subject": "Update regarding your interview", "opening": opening}, "cancellation"
    )
    assert any("cancelled" in p for p in vague)
    clear = messages.check_draft(
        {"subject": "Your interview has been cancelled", "opening": opening}, "cancellation"
    )
    assert clear == []


def test_the_standard_wording_passes_its_own_checks():
    for kind in ("invitation", "reschedule", "cancellation"):
        assert messages.check_draft(messages.template(kind, "Backend Engineer"), kind) == []
