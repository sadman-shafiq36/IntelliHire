# turns an utterance into text: groq first, the local whisper container when groq is
# down, rate limited or not set up. whisper invents text on near-silence ("thank you",
# "thanks for watching"), so segments it isn't confident were speech are dropped

import asyncio
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

from app.logging_config import get_logger
from app.tools.groq import RateLimited
from app.tools.http import ProviderError

log = get_logger(__name__)

Transcribe = Callable[..., dict[str, Any]]

# whisper's own rule for "this was silence": both must hold
NO_SPEECH_PROB = 0.6
SILENT_LOGPROB = -1.0
# repeated text loops compress very well
MAX_COMPRESSION = 2.4
UNCERTAIN_LOGPROB = -0.8
SHORT_WAIT_S = 5.0
MAX_COOLDOWN_S = 600.0
# after a failure that isn't a rate limit, whisper takes over for a while before groq
# is tried again; a refused key won't fix itself, so that waits longest
FAILURE_COOLDOWN_S = 30.0
# typical inventions on breath or room noise; only dropped when whisper also doubts it
STOCK_PHRASES = {
    "thank you.",
    "thank you",
    "thanks for watching!",
    "thanks for watching.",
    "you",
    "bye.",
    ".",
}


class SpeechServiceDown(Exception):
    pass


@dataclass
class Heard:
    text: str
    uncertain: bool
    provider: str


def clean(result: dict[str, Any], provider: str) -> Heard | None:
    segments = result.get("segments")
    if segments is None:
        text = (result.get("text") or "").strip()
        return Heard(text, False, provider) if text else None
    kept: list[str] = []
    uncertain = False
    for s in segments:
        text = (s.get("text") or "").strip()
        no_speech = float(s.get("no_speech_prob") or 0.0)
        logprob = float(s.get("avg_logprob") or 0.0)
        if not text:
            continue
        if no_speech > NO_SPEECH_PROB and logprob < SILENT_LOGPROB:
            continue
        if float(s.get("compression_ratio") or 0.0) > MAX_COMPRESSION:
            uncertain = True
            continue
        if text.lower() in STOCK_PHRASES and no_speech > 0.3:
            continue
        if logprob < UNCERTAIN_LOGPROB:
            uncertain = True
        kept.append(text)
    text = " ".join(kept).strip()
    return Heard(text, uncertain, provider) if text else None


class RateLimiter:
    # groq allows a fixed number of requests a minute for the whole account, so every
    # room in this process shares one window

    def __init__(self, per_minute: int, clock: Callable[[], float] = time.monotonic) -> None:
        self.per_minute = max(1, per_minute)
        self.clock = clock
        self._sent: deque[float] = deque()
        self._lock = asyncio.Lock()

    def wait_needed(self) -> float:
        now = self.clock()
        while self._sent and now - self._sent[0] >= 60:
            self._sent.popleft()
        if len(self._sent) < self.per_minute:
            return 0.0
        return 60 - (now - self._sent[0])

    async def acquire(self) -> None:
        async with self._lock:
            while (wait := self.wait_needed()) > 0:
                await asyncio.sleep(wait)
            self._sent.append(self.clock())


class SpeechToText:
    def __init__(
        self,
        groq: Transcribe | None,
        whisper: Transcribe | None,
        limiter: RateLimiter,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Any] = asyncio.sleep,
    ) -> None:
        self.groq = groq
        self.whisper = whisper
        self.limiter = limiter
        self.clock = clock
        self.sleep = sleep
        self.cool_until = 0.0
        self.last_provider: str | None = None

    def describe(self) -> dict[str, Any]:
        # what the interviewer's room page shows about transcription
        if self.groq is None and self.whisper is None:
            return {"state": "off", "message": "No speech service is set up."}
        if self.groq is None:
            return {
                "state": "running",
                "provider": "whisper",
                "message": "Transcribing with the local Whisper model (Groq is not set up).",
            }
        if self.clock() < self.cool_until:
            return {
                "state": "degraded",
                "provider": "whisper",
                "message": "Groq is unavailable, so the local Whisper model is transcribing. "
                "Text may appear a little later.",
            }
        return {"state": "running", "provider": "groq", "message": "Transcribing with Groq."}

    async def transcribe(self, wav: bytes, prompt: str | None = None) -> Heard | None:
        if self.groq is not None and self.clock() >= self.cool_until:
            heard, done = await self._try_groq(wav, prompt)
            if done:
                return heard
        if self.whisper is not None:
            try:
                result = await asyncio.to_thread(self.whisper, wav)
                self.last_provider = "whisper"
                return clean(result, "whisper")
            except (ProviderError, httpx.HTTPError) as exc:
                log.warning("speech_fallback_failed", error_type=type(exc).__name__)
        raise SpeechServiceDown()

    async def _try_groq(self, wav: bytes, prompt: str | None) -> tuple[Heard | None, bool]:
        for attempt in range(2):
            await self.limiter.acquire()
            try:
                result = await asyncio.to_thread(self.groq, wav, prompt)
            except RateLimited as exc:
                if attempt == 0 and exc.retry_after <= SHORT_WAIT_S:
                    await self.sleep(exc.retry_after)
                    continue
                # a long wait would hold up the live transcript, so whisper covers it
                self.cool_until = self.clock() + min(exc.retry_after, MAX_COOLDOWN_S)
                log.warning("speech_rate_limited", provider="groq", wait_s=exc.retry_after)
                return None, False
            except (ProviderError, httpx.HTTPError) as exc:
                log.warning("speech_call_failed", provider="groq", error_type=type(exc).__name__)
                status = getattr(exc, "status", None)
                if attempt == 0 and status not in (400, 401, 403, 413):
                    await self.sleep(1.0)
                    continue
                refused = status in (401, 403)
                self.cool_until = self.clock() + (MAX_COOLDOWN_S if refused else FAILURE_COOLDOWN_S)
                return None, False
            self.last_provider = "groq"
            return clean(result, "groq"), True
        return None, False
