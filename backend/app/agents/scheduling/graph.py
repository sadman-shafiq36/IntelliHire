# scheduling one interview, rescheduling it, or cancelling it. calendar and email go
# through the mcp server's tools; the ai reads "when" and drafts the email
#
#   schedule / reschedule:
#     plan -> [interpret] -> find_slot -> book -> draft -> check -> send -> END
#   cancel:
#     plan -> draft -> check -> send -> release -> END
#
#   interpret -> interpret   windows unusable, one more try with the problems
#   check     -> draft       draft breaks a rule, one more try, then a plain template
#   send fails               the calendar change is undone, nothing is half done
#   find_slot -> END         conflict (explicit time is busy) or no free slot

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from langgraph.graph import END, StateGraph

from app.agents.scheduling import messages, slots
from app.agents.scheduling.llm import Drafter, Interpreter, gemini_drafter, gemini_interpreter
from app.agents.state import SchedulingState
from app.errors import AppError
from app.tools.mcp_client import SchedulingTools

MAX_INTERPRET_ATTEMPTS = 2
MAX_DRAFT_ATTEMPTS = 2


@dataclass(frozen=True)
class Contact:
    # kept out of graph state on purpose, see SchedulingState
    candidate_name: str
    candidate_email: str
    join_url: str | None


def _tz(state: SchedulingState) -> ZoneInfo:
    return ZoneInfo(state["time_zone"])


def _now(state: SchedulingState) -> datetime:
    return slots.parse(state["now"], _tz(state))


def _duration(state: SchedulingState) -> timedelta:
    return timedelta(minutes=state["duration_minutes"])


def _fail(message: str, outcome: str = "failed", **extra: Any) -> dict:
    return {"outcome": outcome, "message": message, **extra}


def plan(state: SchedulingState) -> dict:
    if state["action"] == "cancel":
        return {}
    if state.get("start"):
        start = slots.parse(state["start"], _tz(state))
        if start <= _now(state):
            return _fail("Choose a time in the future.")
        return {"windows": [[start.isoformat(), (start + _duration(state)).isoformat()]]}
    if (state.get("preference") or "").strip():
        return {}
    return _fail("Choose a time, or describe when the interview should be.")


def make_interpret(interpreter: Interpreter, search_days: int):
    def interpret(state: SchedulingState) -> dict:
        attempts = state.get("interpret_attempts", 0) + 1
        now = _now(state)
        payload = {
            "preference": state["preference"],
            "now_local": now.strftime("%Y-%m-%dT%H:%M"),
            "weekday": now.strftime("%A"),
            "time_zone": state["time_zone"],
            "duration_minutes": state["duration_minutes"],
            "feedback": state.get("feedback", []),
        }
        try:
            result, model = interpreter(payload)
        except AppError as exc:
            return _fail(exc.message, errors=[_err("interpret", exc)])
        windows, problems = slots.check_windows(
            result.get("windows", []), _tz(state), now, search_days
        )
        if not windows and attempts < MAX_INTERPRET_ATTEMPTS:
            return {
                "interpret_attempts": attempts,
                "feedback": problems or ["No usable window was returned."],
                "model_name": model,
            }
        if not windows:
            return _fail(
                "The agent could not work out a time from that. Pick an exact time instead.",
                interpret_attempts=attempts,
            )
        return {
            "interpret_attempts": attempts,
            "windows": [[s.isoformat(), e.isoformat()] for s, e in windows],
            "understood": (result.get("understood") or "").strip()[:300],
            "feedback": [],
            "model_name": model,
        }

    return interpret


def make_find_slot(tools: SchedulingTools, work_start: int, work_end: int):
    def find_slot(state: SchedulingState) -> dict:
        tz = _tz(state)
        windows = [(slots.parse(s, tz), slots.parse(e, tz)) for s, e in state["windows"]]
        duration = _duration(state)
        explicit = bool(state.get("start"))
        busy: list[tuple[datetime, datetime]] = []
        raw_busy: list[dict[str, str]] = []
        warnings: list[str] = []
        if tools.channel == "google":
            try:
                raw_busy = tools.call(
                    "calendar_free_busy",
                    {
                        "time_min": min(w[0] for w in windows).isoformat(),
                        "time_max": max(w[1] for w in windows).isoformat(),
                        "time_zone": state["time_zone"],
                    },
                )["busy"]
            except AppError as exc:
                return _fail(exc.message, errors=[_err("find_slot", exc)])
            busy = slots.busy_periods(raw_busy, tz)
            existing = state.get("existing") or {}
            if existing.get("start"):
                # a reschedule must not clash with the interview it is moving
                own = (slots.parse(existing["start"], tz), slots.parse(existing["end"], tz))
                busy = [b for b in busy if b != own]
        else:
            warnings.append("No Google Calendar is connected, so availability was not checked.")

        if explicit:
            start = windows[0][0]
            if slots.overlaps(start, start + duration, busy) and not state.get("allow_conflict"):
                return _fail(
                    "Your calendar is busy at that time. Pick another time, or schedule it anyway.",
                    outcome="conflict",
                    busy=[
                        {"start": s.isoformat(), "end": e.isoformat()}
                        for s, e in busy
                        if s < start + duration and start < e
                    ],
                )
        else:
            start = slots.first_free_slot(
                windows, busy, duration, _now(state), work_start, work_end
            )
            if start is None:
                return _fail(
                    "There is no free slot in working hours at the times you described. "
                    "Try a wider range or pick an exact time.",
                    outcome="no_slot",
                )
        return {
            "slot_start": start.isoformat(),
            "slot_end": (start + duration).isoformat(),
            "warnings": warnings,
        }

    return find_slot


def make_book(tools: SchedulingTools, contact: Contact):
    def book(state: SchedulingState) -> dict:
        if tools.channel != "google":
            return {}
        existing = state.get("existing") or {}
        try:
            if state["action"] == "reschedule" and existing.get("event_id"):
                event = tools.call(
                    "calendar_update_event",
                    {
                        "event_id": existing["event_id"],
                        "start": state["slot_start"],
                        "end": state["slot_end"],
                        "time_zone": state["time_zone"],
                    },
                )
                return {"event_id": event.get("event_id") or existing["event_id"]}
            # no video conference: the interview is held in intellihire's own room
            event = tools.call(
                "calendar_create_event",
                {
                    "summary": f"Interview: {state['job_title']}",
                    # the interviewer's own calendar, so the candidate's name is useful here
                    "description": f"Interview with {contact.candidate_name} for "
                    f"{state['job_title']}, in IntelliHire: {state.get('interviewer_url') or ''}",
                    "location": messages.ROOM,
                    "start": state["slot_start"],
                    "end": state["slot_end"],
                    "time_zone": state["time_zone"],
                    "attendees": [contact.candidate_email],
                },
            )
        except AppError as exc:
            return _fail(exc.message, errors=[_err("book", exc)])
        return {"event_id": event.get("event_id"), "event_created": True}

    return book


def _kind(state: SchedulingState) -> str:
    return {"schedule": "invitation", "reschedule": "reschedule", "cancel": "cancellation"}[
        state["action"]
    ]


def make_draft(drafter: Drafter):
    def draft(state: SchedulingState) -> dict:
        attempts = state.get("draft_attempts", 0) + 1
        kind = _kind(state)
        payload = {
            "kind": kind,
            "job_title": state["job_title"],
            "note": state.get("note") if kind != "cancellation" else state.get("reason"),
            "feedback": state.get("feedback", []),
        }
        try:
            result, model = drafter(payload)
        except AppError:
            # the email still goes out, in the plain wording
            return {
                "draft_attempts": MAX_DRAFT_ATTEMPTS,
                "draft": messages.template(kind, state["job_title"]),
                "used_template": True,
                "feedback": [],
                "warnings": ["The AI could not draft the email, so the standard wording was used."],
            }
        return {"draft_attempts": attempts, "draft": result, "model_name": model}

    return draft


def check(state: SchedulingState) -> dict:
    if state.get("used_template"):
        return {"feedback": []}
    problems = messages.check_draft(state["draft"], _kind(state))
    if problems and state["draft_attempts"] >= MAX_DRAFT_ATTEMPTS:
        return {
            "draft": messages.template(_kind(state), state["job_title"]),
            "used_template": True,
            "feedback": [],
            "warnings": ["The AI's email broke a rule twice, so the standard wording was used."],
        }
    return {"feedback": problems}


def make_send(tools: SchedulingTools, contact: Contact, signature: messages.Signature):
    def send(state: SchedulingState) -> dict:
        tz = _tz(state)
        existing = state.get("existing") or {}
        # stored times come back in utc; the email speaks the interviewer's time zone
        if state["action"] == "cancel":
            start = slots.parse(existing["start"], tz).astimezone(tz)
            end = slots.parse(existing["end"], tz).astimezone(tz)
        else:
            start = slots.parse(state["slot_start"], tz).astimezone(tz)
            end = slots.parse(state["slot_end"], tz).astimezone(tz)
        kind = _kind(state)
        email = messages.compose(
            kind,
            state["draft"],
            contact.candidate_name,
            state["job_title"],
            start,
            end,
            state["time_zone"],
            contact.join_url,
            signature,
        )
        uid = existing.get("ics_uid") or messages.new_uid()
        sequence = (existing.get("ics_sequence") or 0) + (1 if existing else 0)
        method = "CANCEL" if state["action"] == "cancel" else "REQUEST"
        ics = messages.build_ics(
            uid,
            sequence,
            method,
            f"Interview: {state['job_title']}",
            f"Join the interview: {contact.join_url}" if contact.join_url else "Interview",
            start,
            end,
            state.get("organizer_email"),
            signature.name or None,
            contact.candidate_email,
            contact.candidate_name,
            contact.join_url or messages.ROOM,
        )
        tool = "gmail_send" if tools.channel == "google" else "smtp_send"
        try:
            sent = tools.call(
                tool,
                {
                    "to": contact.candidate_email,
                    "subject": email.subject,
                    "body": email.text,
                    "html": email.html,
                    "ics": ics,
                    "ics_method": method,
                },
            )
        except AppError as exc:
            return {**_fail(exc.message, errors=[_err("send", exc)]), **_undo(tools, state)}
        return {
            "message_id": sent.get("message_id", ""),
            "ics_uid": uid,
            "ics_sequence": sequence,
            "outcome": {
                "schedule": "scheduled",
                "reschedule": "rescheduled",
                "cancel": "cancelled",
            }[state["action"]],
        }

    return send


def _undo(tools: SchedulingTools, state: SchedulingState) -> dict:
    # the email failed: put the calendar back so the candidate and the calendar agree
    existing = state.get("existing") or {}
    try:
        if state["action"] == "schedule" and state.get("event_created"):
            tools.call("calendar_delete_event", {"event_id": state["event_id"]})
        elif (
            state["action"] == "reschedule"
            and existing.get("event_id")
            and tools.channel == "google"
        ):
            tools.call(
                "calendar_update_event",
                {
                    "event_id": existing["event_id"],
                    "start": existing["start"],
                    "end": existing["end"],
                    "time_zone": state["time_zone"],
                },
            )
    except AppError:
        return {
            "warnings": [
                "The email failed and the calendar could not be put back. Check your calendar."
            ]
        }
    return {}


def make_release(tools: SchedulingTools):
    def release(state: SchedulingState) -> dict:
        # cancel: the event is removed only after the candidate has been told
        event_id = (state.get("existing") or {}).get("event_id")
        if tools.channel != "google" or not event_id:
            return {}
        try:
            tools.call("calendar_delete_event", {"event_id": event_id})
        except AppError:
            return {
                "warnings": [
                    "The candidate was told, but the calendar event could not be removed. "
                    "Delete it from your calendar."
                ]
            }
        return {}

    return release


def after_plan(state: SchedulingState) -> str:
    if state.get("outcome") == "failed":
        return END
    if state["action"] == "cancel":
        return "draft"
    return "find_slot" if state.get("windows") else "interpret"


def after_interpret(state: SchedulingState) -> str:
    if state.get("outcome") == "failed":
        return END
    return "find_slot" if state.get("windows") else "interpret"


def after_find_slot(state: SchedulingState) -> str:
    return END if state.get("outcome") else "book"


def after_book(state: SchedulingState) -> str:
    return END if state.get("outcome") else "draft"


def after_check(state: SchedulingState) -> str:
    return "draft" if state.get("feedback") else "send"


def after_send(state: SchedulingState) -> str:
    if state.get("outcome") == "cancelled":
        return "release"
    return END


def build_scheduling_graph(
    tools: SchedulingTools,
    contact: Contact,
    checkpointer: Any = None,
    interpreter: Interpreter = gemini_interpreter,
    drafter: Drafter = gemini_drafter,
    signature: messages.Signature | None = None,
    work_start: int = 9,
    work_end: int = 17,
    search_days: int = 14,
):
    # built per run: the tools carry this run's google token and the contact its address
    builder = StateGraph(SchedulingState)
    builder.add_node("plan", plan)
    builder.add_node("interpret", make_interpret(interpreter, search_days))
    builder.add_node("find_slot", make_find_slot(tools, work_start, work_end))
    builder.add_node("book", make_book(tools, contact))
    builder.add_node("draft", make_draft(drafter))
    builder.add_node("check", check)
    builder.add_node("send", make_send(tools, contact, signature or messages.Signature("", "", "")))
    builder.add_node("release", make_release(tools))

    builder.set_entry_point("plan")
    builder.add_conditional_edges(
        "plan",
        after_plan,
        {"draft": "draft", "find_slot": "find_slot", "interpret": "interpret", END: END},
    )
    builder.add_conditional_edges(
        "interpret", after_interpret, {"find_slot": "find_slot", "interpret": "interpret", END: END}
    )
    builder.add_conditional_edges("find_slot", after_find_slot, {"book": "book", END: END})
    builder.add_conditional_edges("book", after_book, {"draft": "draft", END: END})
    builder.add_edge("draft", "check")
    builder.add_conditional_edges("check", after_check, {"draft": "draft", "send": "send"})
    builder.add_conditional_edges("send", after_send, {"release": "release", END: END})
    builder.add_edge("release", END)
    return builder.compile(checkpointer=checkpointer)


def _err(node: str, exc: AppError) -> dict:
    return {"node": node, "code": type(exc).__name__, "message": exc.message, "item_ref": None}
