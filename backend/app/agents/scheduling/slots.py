# deterministic slot finding: the ai only says roughly when; code decides the exact time
# from the calendar's busy periods, working hours and the interview length

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

STEP = timedelta(minutes=15)


def parse(value: str, tz: ZoneInfo) -> datetime:
    # a time without an offset is read in the interviewer's time zone
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=tz)


def overlaps(start: datetime, end: datetime, busy: list[tuple[datetime, datetime]]) -> bool:
    return any(start < b_end and b_start < end for b_start, b_end in busy)


def busy_periods(raw: list[dict[str, str]], tz: ZoneInfo) -> list[tuple[datetime, datetime]]:
    return sorted((parse(b["start"], tz), parse(b["end"], tz)) for b in raw)


def round_up(dt: datetime) -> datetime:
    minutes = (dt.minute // 15 + (1 if dt.minute % 15 or dt.second or dt.microsecond else 0)) * 15
    return dt.replace(minute=0, second=0, microsecond=0) + timedelta(minutes=minutes)


def first_free_slot(
    windows: list[tuple[datetime, datetime]],
    busy: list[tuple[datetime, datetime]],
    duration: timedelta,
    now: datetime,
    work_start: int,
    work_end: int,
) -> datetime | None:
    # earliest start in the first window that has room, on a quarter hour, in working
    # hours, not in the past and clear of every busy period
    earliest = round_up(now + timedelta(hours=1))
    for w_start, w_end in sorted(windows):
        t = round_up(max(w_start, earliest))
        while t + duration <= w_end:
            day_start = t.replace(hour=work_start, minute=0, second=0, microsecond=0)
            day_end = t.replace(hour=work_end, minute=0, second=0, microsecond=0)
            if t < day_start:
                t = day_start
                continue
            if t + duration > day_end:
                t = (t + timedelta(days=1)).replace(hour=work_start, minute=0)
                continue
            if t.weekday() >= 5:
                t = (t + timedelta(days=1)).replace(hour=work_start, minute=0)
                continue
            if not overlaps(t, t + duration, busy):
                return t
            t += STEP
    return None


def check_windows(
    raw: list[dict[str, Any]], tz: ZoneInfo, now: datetime, search_days: int
) -> tuple[list[tuple[datetime, datetime]], list[str]]:
    # windows from the ai are kept only if they parse, run forwards and fall in the
    # next search_days; anything else is dropped with a reason
    kept, problems = [], []
    limit = now + timedelta(days=search_days)
    for w in raw:
        try:
            start, end = parse(w["start"], tz), parse(w["end"], tz)
        except (KeyError, TypeError, ValueError):
            problems.append(f"Window {w} is not a pair of ISO date-times.")
            continue
        if end <= start:
            problems.append(f"Window {w['start']} to {w['end']} ends before it starts.")
            continue
        if end <= now:
            problems.append(f"Window {w['start']} to {w['end']} is in the past.")
            continue
        if start > limit:
            problems.append(f"Window starting {w['start']} is more than {search_days} days away.")
            continue
        kept.append((max(start, now), min(end, limit)))
    return kept, problems
