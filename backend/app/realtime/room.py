# the transcriber in one interview room: a hidden participant that hears each person's
# own microphone track. the speaker is whoever's track it was, so there is no guessing
# who said what, even when both talk at once

import asyncio
import json
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from livekit import rtc

from app.config import settings
from app.logging_config import get_logger
from app.models.enums import SpeakerRole
from app.realtime import status
from app.realtime.audio import FRAME_MS, SAMPLE_RATE, SpeechSegmenter
from app.realtime.pipeline import SaveFn, TranscriptPipeline
from app.realtime.stt import SpeechToText
from app.services.interview_sessions import role_of
from app.tools import livekit

log = get_logger(__name__)

BEAT_SECONDS = 5
DRAIN_SECONDS = 90


class RoomTranscriber:
    def __init__(self, interview_id: int, stt: SpeechToText, save: SaveFn) -> None:
        self.interview_id = interview_id
        self.stt = stt
        self.save = save
        self.room = rtc.Room()
        self.pipeline = TranscriptPipeline(stt, save, self._push)
        self.last_human = time.monotonic()
        self._stopping = asyncio.Event()
        self._lost = asyncio.Event()
        self._listeners: set[asyncio.Task] = set()

    def stop(self) -> None:
        self._stopping.set()

    def idle_seconds(self) -> float:
        return time.monotonic() - self.last_human

    async def run(self) -> None:
        name = livekit.room_name(self.interview_id)
        token = livekit.participant_token(
            livekit.TRANSCRIBER_IDENTITY, "IntelliHire transcriber", name, "transcriber"
        )
        self.room.on("track_subscribed", self._on_track)
        self.room.on("disconnected", lambda *_: self._lost.set())
        url = settings.livekit_url.replace("http", "ws", 1)
        await self.room.connect(url, token, rtc.RoomOptions(auto_subscribe=True))
        log.info("transcriber_joined", interview_id=self.interview_id)
        worker = asyncio.create_task(self.pipeline.run())
        beat = asyncio.create_task(self._beat())
        stop = asyncio.create_task(self._stopping.wait())
        lost = asyncio.create_task(self._lost.wait())
        try:
            await asyncio.wait({stop, lost}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            # finish what was heard: close every utterance, transcribe the queue, then leave
            self._stopping.set()
            for task in (stop, lost):
                task.cancel()
            await asyncio.gather(*self._listeners, return_exceptions=True)
            self.pipeline.close()
            try:
                await asyncio.wait_for(worker, DRAIN_SECONDS)
            except TimeoutError:
                log.warning("transcript_drain_timed_out", backlog=self.pipeline.backlog)
            beat.cancel()
            await asyncio.to_thread(status.clear, self.interview_id)
            await self.room.disconnect()
            log.info("transcriber_left", interview_id=self.interview_id, lost=self._lost.is_set())

    def _on_track(self, track: rtc.Track, _pub: Any, participant: rtc.RemoteParticipant) -> None:
        if track.kind != rtc.TrackKind.KIND_AUDIO:
            return
        role = role_of(participant.identity)
        if role is None:
            return
        task = asyncio.create_task(self._listen(track, participant.identity, role))
        self._listeners.add(task)
        task.add_done_callback(self._listeners.discard)

    async def _listen(self, track: rtc.Track, identity: str, role: SpeakerRole) -> None:
        segmenter = SpeechSegmenter(
            settings.speech_silence_ms, settings.speech_max_seconds * 1000, settings.speech_min_ms
        )
        stream = rtc.AudioStream(
            track, sample_rate=SAMPLE_RATE, num_channels=1, frame_size_ms=FRAME_MS
        )
        frames = aiter(stream)
        quiet = settings.speech_silence_ms / 1000

        def put(utterances: list) -> None:
            for u in utterances:
                self.pipeline.put(identity, role, u)

        log.info("transcriber_listening", interview_id=self.interview_id, role=role.value)
        try:
            while not self._stopping.is_set():
                try:
                    event = await asyncio.wait_for(anext(frames), quiet)
                except TimeoutError:
                    # a muted microphone sends nothing, which ends the utterance too
                    put(segmenter.flush(datetime.now(UTC)))
                    continue
                except StopAsyncIteration:
                    break
                put(segmenter.feed(event.frame.data.tobytes(), datetime.now(UTC)))
        finally:
            put(segmenter.flush(datetime.now(UTC)))
            await stream.aclose()

    def _interviewers(self) -> list[str]:
        return [
            p.identity
            for p in self.room.remote_participants.values()
            if role_of(p.identity) == SpeakerRole.INTERVIEWER
        ]

    async def _push(self, turn: dict[str, Any]) -> None:
        # only interviewers get the live lines
        to = self._interviewers()
        if to:
            await self.room.local_participant.publish_data(
                json.dumps({"type": "turn", "turn": turn}),
                reliable=True,
                topic="transcript",
                destination_identities=to,
            )

    async def _beat(self) -> None:
        while True:
            if any(role_of(p.identity) for p in self.room.remote_participants.values()):
                self.last_human = time.monotonic()
            report = {**self.stt.describe(), "backlog": self.pipeline.backlog}
            try:
                await asyncio.to_thread(status.publish, self.interview_id, report)
            except Exception as exc:
                log.warning("transcription_status_not_saved", error_type=type(exc).__name__)
            await asyncio.sleep(BEAT_SECONDS)


def make_saver(session_factory: Callable[[], Any], interview_id: int) -> SaveFn:
    from app.services.interview_sessions import save_turn

    def save(role, text, started_at, ended_at, uncertain, provider) -> dict[str, Any]:
        with session_factory() as db:
            return save_turn(
                db, interview_id, role, text, started_at, ended_at, uncertain, provider
            )

    return save
