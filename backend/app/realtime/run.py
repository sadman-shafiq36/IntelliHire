# the transcriber service: python -m app.realtime.run
# every few seconds it looks for interviews in progress, joins any room it isn't in
# (including after its own restart, or a dropped connection), and leaves rooms whose
# interview has ended. a room left empty for too long is ended for the interviewer

import asyncio
import contextlib
import signal
import time

from app.config import settings
from app.db.session import get_session_factory
from app.logging_config import configure_logging, get_logger
from app.realtime import status
from app.realtime.room import Identifier, RoomTranscriber, make_saver
from app.realtime.stt import RateLimiter, SpeechToText
from app.services import interview_sessions
from app.tools import groq, livekit, whisper

log = get_logger(__name__)

RETRY_SECONDS = 5


def _live() -> set[int]:
    with get_session_factory()() as db:
        return set(interview_sessions.live_interviews(db))


def _end_idle(interview_id: int) -> None:
    with get_session_factory()() as db:
        interview_sessions.end(db, interview_id, reason="empty room")


def build_stt() -> SpeechToText:
    return SpeechToText(
        groq.transcribe if groq.configured() else None,
        whisper.transcribe,
        RateLimiter(settings.groq_requests_per_minute),
    )


async def main() -> None:
    configure_logging()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    stt = build_stt()
    log.info("transcriber_started", groq=groq.configured(), livekit=livekit.configured())
    rooms: dict[int, tuple[RoomTranscriber, asyncio.Task]] = {}
    failed_at: dict[int, float] = {}

    while not stop.is_set():
        try:
            await asyncio.to_thread(status.heartbeat)
            live = await asyncio.to_thread(_live) if livekit.configured() else set()
        except Exception as exc:
            log.warning("transcriber_poll_failed", error_type=type(exc).__name__)
            live = set(rooms)

        for interview_id, (room, task) in list(rooms.items()):
            if task.done():
                rooms.pop(interview_id)
                if not task.cancelled() and task.exception() is not None:
                    log.warning(
                        "transcriber_room_failed",
                        interview_id=interview_id,
                        error_type=type(task.exception()).__name__,
                    )
                    failed_at[interview_id] = time.monotonic()
            elif interview_id not in live:
                room.stop()
            elif room.idle_seconds() > settings.session_idle_end_minutes * 60:
                log.info("interview_left_empty", interview_id=interview_id)
                try:
                    await asyncio.to_thread(_end_idle, interview_id)
                except Exception as exc:
                    log.warning("idle_end_failed", error_type=type(exc).__name__)
                room.stop()

        for interview_id in live - set(rooms):
            if time.monotonic() - failed_at.get(interview_id, 0) < RETRY_SECONDS:
                continue
            room = RoomTranscriber(
                interview_id,
                stt,
                make_saver(get_session_factory(), interview_id),
                Identifier(get_session_factory(), interview_id),
            )
            rooms[interview_id] = (room, asyncio.create_task(room.run()))

        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), settings.transcriber_poll_seconds)

    # shutting down: every room finishes what it heard before the process exits
    for room, _ in rooms.values():
        room.stop()
    await asyncio.gather(*(task for _, task in rooms.values()), return_exceptions=True)
    log.info("transcriber_stopped")


if __name__ == "__main__":
    asyncio.run(main())
