# cuts one participant's audio into utterances: speech starts when the level rises
# above the room's background, and ends after a pause. only the utterance being built
# is held in memory, and nothing is written to disk

import io
import math
import wave
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np

SAMPLE_RATE = 16000
FRAME_MS = 20
SAMPLE_WIDTH = 2  # int16


@dataclass
class Utterance:
    pcm: bytes
    started_at: datetime
    ended_at: datetime
    voiced_ms: int
    # true when it was cut at the length limit and the speaker carried on
    cut: bool = False

    @property
    def duration_ms(self) -> int:
        return len(self.pcm) * 1000 // (SAMPLE_RATE * SAMPLE_WIDTH)


def level_db(pcm: bytes) -> float:
    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    if samples.size == 0:
        return -100.0
    rms = math.sqrt(float(np.mean(samples * samples)))
    return 20 * math.log10(rms / 32768) if rms > 0 else -100.0


class SpeechSegmenter:
    # speech is anything clearly above both an absolute floor and the background. the
    # background is a low percentile of the last few seconds: people pause between
    # words, so it settles on the room's noise and a noisy room isn't "talking"
    MIN_LEVEL_DB = -50.0
    ABOVE_BACKGROUND_DB = 10.0
    BACKGROUND_FRAMES = 500  # 10 s
    WARMUP_FRAMES = 5
    START_FRAMES = 3  # 60 ms of speech before an utterance opens
    PREROLL_MS = 300  # kept from before the start so the first syllable isn't clipped
    TAIL_MS = 200  # silence left on the end

    def __init__(self, silence_ms: int = 700, max_ms: int = 15000, min_speech_ms: int = 300):
        self.silence_frames = max(1, silence_ms // FRAME_MS)
        self.max_frames = max(1, max_ms // FRAME_MS)
        self.min_speech_frames = max(1, min_speech_ms // FRAME_MS)
        self.background_db = -70.0
        self._levels: deque[float] = deque(maxlen=self.BACKGROUND_FRAMES)
        self._preroll: deque[tuple[bytes, datetime]] = deque(maxlen=self.PREROLL_MS // FRAME_MS)
        self._frames: list[bytes] = []
        self._start: datetime | None = None
        self._voiced = 0
        self._silent = 0
        self._run = 0
        self._last_at: datetime | None = None

    @property
    def speaking(self) -> bool:
        return self._start is not None

    def _is_speech(self, db: float) -> bool:
        self._levels.append(db)
        if len(self._levels) < self.WARMUP_FRAMES:
            return False
        self.background_db = float(np.percentile(self._levels, 5))
        return db > max(self.MIN_LEVEL_DB, self.background_db + self.ABOVE_BACKGROUND_DB)

    def feed(self, pcm: bytes, at: datetime) -> list[Utterance]:
        # pcm is one 20 ms frame of 16 kHz mono int16, `at` is when it arrived
        self._last_at = at
        speech = self._is_speech(level_db(pcm))
        out: list[Utterance] = []
        if not self.speaking:
            self._run = self._run + 1 if speech else 0
            self._preroll.append((pcm, at))
            if self._run >= self.START_FRAMES:
                self._frames = [f for f, _ in self._preroll]
                self._start = self._preroll[0][1]
                self._voiced = self._run
                self._silent = 0
                self._preroll.clear()
            return out
        self._frames.append(pcm)
        if speech:
            self._voiced += 1
            self._silent = 0
        else:
            self._silent += 1
        if self._silent >= self.silence_frames:
            utterance = self._close(at, trim=True)
            if utterance:
                out.append(utterance)
        elif len(self._frames) >= self.max_frames:
            # a long answer goes out in pieces so the text keeps up with the speaker
            utterance = self._close(at, trim=False, cut=True)
            if utterance:
                out.append(utterance)
            self._start = at
            self._voiced = 0
            self._silent = 0
        return out

    def flush(self, at: datetime | None = None) -> list[Utterance]:
        # end of the track, or no frames for a while (a muted microphone sends nothing)
        if not self.speaking:
            return []
        utterance = self._close(at or self._last_at or datetime.now(), trim=True)
        return [utterance] if utterance else []

    def _close(self, at: datetime, trim: bool, cut: bool = False) -> Utterance | None:
        frames = self._frames
        if trim and self._silent > self.TAIL_MS // FRAME_MS:
            frames = frames[: len(frames) - self._silent + self.TAIL_MS // FRAME_MS]
        voiced, start = self._voiced, self._start
        self._frames, self._start, self._voiced, self._silent, self._run = [], None, 0, 0, 0
        if voiced < self.min_speech_frames or start is None:
            return None
        pcm = b"".join(frames)
        duration = timedelta(milliseconds=len(pcm) * 1000 // (SAMPLE_RATE * SAMPLE_WIDTH))
        return Utterance(pcm, start, start + duration, voiced * FRAME_MS, cut)


def join(parts: list[Utterance], gap_ms: int = 300) -> Utterance:
    # one request instead of several when they queued up behind a rate limit
    silence = b"\x00" * (SAMPLE_RATE * SAMPLE_WIDTH * gap_ms // 1000)
    return Utterance(
        silence.join(p.pcm for p in parts),
        parts[0].started_at,
        parts[-1].ended_at,
        sum(p.voiced_ms for p in parts),
        parts[-1].cut,
    )


def to_wav(pcm: bytes) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(SAMPLE_WIDTH)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm)
    return buffer.getvalue()
