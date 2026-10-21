# one room's utterances, in the order they ended: transcribe, save, push to the
# interviewer. both speakers feed the same queue, so overlapping speech simply gives
# two turns with overlapping times. audio is dropped as soon as it is transcribed

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from app.logging_config import get_logger
from app.models.enums import SpeakerRole
from app.realtime.audio import Utterance, join, to_wav
from app.realtime.stt import Heard, SpeechServiceDown, SpeechToText

log = get_logger(__name__)

UNHEARD = "[not transcribed: the speech service was unavailable]"
MERGE_GAP = timedelta(seconds=3)
MERGE_MAX_MS = 25_000
PROMPT_CHARS = 200
SAVE_ATTEMPTS = 3


@dataclass
class Spoken:
    identity: str
    role: SpeakerRole
    utterance: Utterance


SaveFn = Callable[[SpeakerRole, str, datetime, datetime, bool, str], dict[str, Any]]
PublishFn = Callable[[dict[str, Any]], Awaitable[None]]


class TranscriptPipeline:
    def __init__(
        self,
        stt: SpeechToText,
        save: SaveFn,
        publish: PublishFn,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    ) -> None:
        self.stt = stt
        self.save = save
        self.publish = publish
        self.sleep = sleep
        self._queue: deque[Spoken] = deque()
        self._wake = asyncio.Event()
        self._closed = False
        self._context = ""

    @property
    def backlog(self) -> int:
        return len(self._queue)

    def put(self, identity: str, role: SpeakerRole, utterance: Utterance) -> None:
        self._queue.append(Spoken(identity, role, utterance))
        self._wake.set()

    def close(self) -> None:
        # whatever is queued is still transcribed before run() returns
        self._closed = True
        self._wake.set()

    async def run(self) -> None:
        while True:
            if not self._queue:
                if self._closed:
                    return
                self._wake.clear()
                await self._wake.wait()
                continue
            await self._process(self._take())

    def _take(self) -> Spoken:
        # when a rate limit has let several pieces from the same speaker pile up,
        # they go out as one request
        first = self._queue.popleft()
        parts = [first.utterance]
        while self._queue:
            nxt = self._queue[0]
            if nxt.identity != first.identity:
                break
            if nxt.utterance.started_at - parts[-1].ended_at > MERGE_GAP:
                break
            if sum(p.duration_ms for p in parts) + nxt.utterance.duration_ms > MERGE_MAX_MS:
                break
            parts.append(self._queue.popleft().utterance)
        return Spoken(first.identity, first.role, join(parts) if len(parts) > 1 else parts[0])

    async def _process(self, spoken: Spoken) -> None:
        u = spoken.utterance
        try:
            heard = await self.stt.transcribe(to_wav(u.pcm), self._context or None)
        except SpeechServiceDown:
            # the turn stays in the timeline, marked, rather than vanishing
            heard = Heard(UNHEARD, True, "none")
        if heard is None:
            return
        if heard.provider != "none":
            self._context = heard.text[-PROMPT_CHARS:]
        payload = await self._save(spoken, heard)
        if payload is None:
            return
        try:
            await self.publish(payload)
        except Exception as exc:
            # the interviewer's panel also reads saved turns, so this only delays it
            log.warning("transcript_push_failed", error_type=type(exc).__name__)

    async def _save(self, spoken: Spoken, heard: Heard) -> dict[str, Any] | None:
        u = spoken.utterance
        for attempt in range(1, SAVE_ATTEMPTS + 1):
            try:
                return await asyncio.to_thread(
                    self.save, spoken.role, heard.text, u.started_at, u.ended_at,
                    heard.uncertain, heard.provider,
                )  # fmt: skip
            except Exception as exc:
                log.warning(
                    "transcript_save_failed", attempt=attempt, error_type=type(exc).__name__
                )
                if attempt < SAVE_ATTEMPTS:
                    await self.sleep(attempt)
        log.error("transcript_turn_lost", role=spoken.role.value)
        return None
