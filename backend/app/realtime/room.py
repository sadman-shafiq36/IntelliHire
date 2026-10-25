# the transcriber in one interview room: a hidden participant that hears each person's
# own microphone track. the speaker is whoever's track it was, so there is no guessing
# who said what, even when both talk at once

import asyncio
import json
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import numpy as np
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
# the candidate's camera is looked at for this long, a frame a second, and never stored
IDENTIFY_SECONDS = 15
IDENTIFY_FACES = 5


class Identifier:
    # the face check, kept apart from the room so tests can swap it
    def __init__(self, session_factory: Callable[[], Any], interview_id: int) -> None:
        self.session_factory = session_factory
        self.interview_id = interview_id

    def references(self) -> Any:
        # none (and a "not identified" check saved) when the live face must not be looked at
        from app.services import identity

        with self.session_factory() as db:
            return identity.references_for(db, self.interview_id)

    def record(self, faces: list, refs: Any) -> dict[str, Any]:
        from app.services import identity

        with self.session_factory() as db:
            identity.record_match(db, self.interview_id, faces, refs)
            return identity.current(db, self.interview_id)

    def current(self) -> dict[str, Any]:
        from app.services import identity

        with self.session_factory() as db:
            return identity.current(db, self.interview_id)

    def decided_by_hand(self) -> bool:
        from app.services import identity

        with self.session_factory() as db:
            row = identity.current_row(db, self.interview_id)
            return bool(row and row.method == "manual")

    def retry_requested(self) -> bool:
        from app.services import identity

        return identity.take_retry(self.interview_id)


class RoomTranscriber:
    def __init__(
        self,
        interview_id: int,
        stt: SpeechToText,
        save: SaveFn,
        identifier: Identifier | None = None,
    ) -> None:
        self.interview_id = interview_id
        self.stt = stt
        self.save = save
        self.identifier = identifier
        self._camera: rtc.Track | None = None
        self._identifying: asyncio.Task | None = None
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
        if track.kind == rtc.TrackKind.KIND_VIDEO:
            # only the candidate's camera, and only to check who they are
            if role_of(participant.identity) == SpeakerRole.CANDIDATE and self.identifier:
                self._camera = track
                self._start_identifying()
            return
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

    def _start_identifying(self, asked: bool = False) -> None:
        if self._identifying is None or self._identifying.done():
            self._identifying = asyncio.create_task(self._identify(asked))

    async def _identify(self, asked: bool) -> None:
        identifier, track = self.identifier, self._camera
        if identifier is None or track is None:
            return
        try:
            # a camera that comes back after the interviewer decided doesn't overrule them;
            # pressing retry does
            if not asked and await asyncio.to_thread(identifier.decided_by_hand):
                return
            refs = await asyncio.to_thread(identifier.references)
            faces = await self._look(track) if refs is not None else []
            if refs is None:
                result = await asyncio.to_thread(identifier.current)
            else:
                result = await asyncio.to_thread(identifier.record, faces, refs)
            await self._send("identity", {"type": "identity", "identity": result})
        except Exception as exc:
            log.warning("identity_check_failed", error_type=type(exc).__name__)

    async def _look(self, track: rtc.Track) -> list:
        # a frame a second, turned into a template in memory; the frames are dropped at once
        from app.tools import face

        stream = rtc.VideoStream(track, format=rtc.VideoBufferType.RGB24)
        frames = aiter(stream)
        faces: list = []
        deadline = time.monotonic() + IDENTIFY_SECONDS
        next_at = 0.0
        try:
            while time.monotonic() < deadline and not self._stopping.is_set():
                try:
                    event = await asyncio.wait_for(anext(frames), 2)
                except (TimeoutError, StopAsyncIteration):
                    break
                if time.monotonic() < next_at:
                    continue
                next_at = time.monotonic() + 1
                frame = event.frame
                if len(frame.data) != frame.width * frame.height * 3:
                    continue
                pixels = np.frombuffer(frame.data, dtype=np.uint8).reshape(
                    frame.height, frame.width, 3
                )
                faces.append(await asyncio.to_thread(face.embed_rgb, pixels.copy()))
                del pixels, frame
                if sum(1 for f in faces if f.vector is not None) >= IDENTIFY_FACES:
                    break
        finally:
            await stream.aclose()
        return faces

    def _interviewers(self) -> list[str]:
        return [
            p.identity
            for p in self.room.remote_participants.values()
            if role_of(p.identity) == SpeakerRole.INTERVIEWER
        ]

    async def _push(self, turn: dict[str, Any]) -> None:
        await self._send("transcript", {"type": "turn", "turn": turn})

    async def _send(self, topic: str, message: dict[str, Any]) -> None:
        # only interviewers get the live lines and the identity check
        to = self._interviewers()
        if to:
            await self.room.local_participant.publish_data(
                json.dumps(message, default=str),
                reliable=True,
                topic=topic,
                destination_identities=to,
            )

    async def _beat(self) -> None:
        while True:
            if any(role_of(p.identity) for p in self.room.remote_participants.values()):
                self.last_human = time.monotonic()
            if self.identifier and self._camera is not None:
                try:
                    if await asyncio.to_thread(self.identifier.retry_requested):
                        self._start_identifying(asked=True)
                except Exception as exc:
                    log.warning("identity_retry_not_read", error_type=type(exc).__name__)
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
