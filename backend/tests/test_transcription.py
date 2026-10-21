import asyncio
import io
import wave
from datetime import UTC, datetime, timedelta

import httpx
import numpy as np
import pytest

from app.models.enums import InterviewStatus, SpeakerRole
from app.models.interview import Interview, TranscriptTurn
from app.realtime.audio import FRAME_MS, SAMPLE_RATE, SpeechSegmenter, Utterance, to_wav
from app.realtime.pipeline import UNHEARD, TranscriptPipeline
from app.realtime.stt import Heard, RateLimiter, SpeechServiceDown, SpeechToText, clean
from app.tools import groq, http, whisper

T0 = datetime(2026, 10, 16, 9, 0, tzinfo=UTC)
SAMPLES = SAMPLE_RATE * FRAME_MS // 1000
RNG = np.random.default_rng(7)


def tone(db: float) -> bytes:
    t = np.arange(SAMPLES) / SAMPLE_RATE
    amplitude = 32767 * 10 ** (db / 20) * np.sqrt(2)
    return (amplitude * np.sin(2 * np.pi * 220 * t)).astype(np.int16).tobytes()


def noise(db: float) -> bytes:
    scale = 32767 * 10 ** (db / 20)
    return (RNG.normal(0, scale, SAMPLES)).clip(-32768, 32767).astype(np.int16).tobytes()


SILENCE = b"\x00" * SAMPLES * 2


def play(segmenter: SpeechSegmenter, parts: list[tuple[bytes, int]], start: int = 0):
    # parts are (frame, milliseconds); returns utterances and the frame clock at the end
    out: list[Utterance] = []
    n = start
    for frame, ms in parts:
        for _ in range(ms // FRAME_MS):
            out += segmenter.feed(frame, T0 + timedelta(milliseconds=n * FRAME_MS))
            n += 1
    return out, n


# --- cutting audio into utterances -------------------------------------------------------


def test_one_utterance_between_pauses_with_its_start_time():
    seg = SpeechSegmenter(silence_ms=700)
    out, _ = play(seg, [(SILENCE, 1000), (tone(-20), 1200), (SILENCE, 1000)])
    [u] = out
    # the start includes a little audio from just before the speech
    assert T0 + timedelta(milliseconds=600) <= u.started_at <= T0 + timedelta(milliseconds=1000)
    # 1.2 s of speech, the pre-roll and a short tail, not the whole pause
    assert 1200 <= u.duration_ms <= 1800
    assert u.voiced_ms >= 1100 and not u.cut


def test_a_click_is_not_an_utterance():
    seg = SpeechSegmenter(min_speech_ms=300)
    out, _ = play(seg, [(SILENCE, 500), (tone(-20), 100), (SILENCE, 1500)])
    assert out == []


def test_short_pauses_inside_a_sentence_do_not_split_it():
    seg = SpeechSegmenter(silence_ms=700)
    parts = [(SILENCE, 500)]
    for _ in range(4):
        parts += [(tone(-20), 600), (SILENCE, 300)]
    out, _ = play(seg, parts + [(SILENCE, 1000)])
    assert len(out) == 1


def test_a_long_answer_is_sent_in_pieces():
    seg = SpeechSegmenter(max_ms=5000)
    # words with the short gaps real speech has between them
    words = [(tone(-20), 400), (SILENCE, 100)] * 24
    out, _ = play(seg, [(SILENCE, 200), *words, (SILENCE, 1000)])
    assert [u.cut for u in out] == [True, True, False]
    assert all(u.duration_ms <= 5000 for u in out)
    # the pieces follow on from each other
    assert out[1].started_at >= out[0].started_at + timedelta(milliseconds=4900)


def test_a_steady_noisy_room_is_not_speech_but_a_voice_over_it_is():
    seg = SpeechSegmenter()
    out, _ = play(seg, [(noise(-40), 3000), (tone(-15), 1000), (noise(-40), 2000)])
    assert len(out) == 1
    assert out[0].started_at >= T0 + timedelta(milliseconds=2600)


def test_flush_closes_an_open_utterance():
    seg = SpeechSegmenter()
    out, _ = play(seg, [(SILENCE, 200), (tone(-20), 800)])
    assert out == [] and seg.speaking
    [u] = seg.flush()
    assert u.voiced_ms >= 700 and not seg.speaking
    assert seg.flush() == []


def test_wav_is_16khz_mono_in_memory():
    data = to_wav(tone(-20) * 10)
    with wave.open(io.BytesIO(data)) as w:
        assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (16000, 1, 2)
        assert w.getnframes() == SAMPLES * 10


# --- cleaning what the model returns -------------------------------------------------------


def seg(text, no_speech=0.01, logprob=-0.2, compression=1.2):
    return {
        "text": text,
        "no_speech_prob": no_speech,
        "avg_logprob": logprob,
        "compression_ratio": compression,
    }


def test_silence_and_stock_phrases_are_dropped_real_speech_kept():
    heard = clean(
        {
            "segments": [
                seg(" I led the migration to Postgres."),
                seg(" Thank you.", no_speech=0.7, logprob=-1.3),
                seg(" Thanks for watching!", no_speech=0.4, logprob=-0.5),
            ]
        },
        "groq",
    )
    assert heard == Heard("I led the migration to Postgres.", False, "groq")
    # a genuine "thank you" is kept
    assert clean({"segments": [seg("Thank you.")]}, "groq").text == "Thank you."
    assert clean({"segments": [seg(" ", no_speech=0.9, logprob=-2)]}, "groq") is None


def test_low_confidence_and_repetition_loops_are_marked_uncertain():
    heard = clean({"segments": [seg("Kubernetes, mostly.", logprob=-1.1)]}, "whisper")
    assert heard.uncertain and heard.text == "Kubernetes, mostly."
    looped = clean({"segments": [seg("Yes."), seg("the the the the the", compression=3.1)]}, "groq")
    assert looped.text == "Yes." and looped.uncertain


# --- groq first, local whisper when it can't -----------------------------------------------


@pytest.fixture
def speech_services(monkeypatch: pytest.MonkeyPatch):
    from app.config import settings

    monkeypatch.setattr(settings, "groq_api_key", "gsk-test")
    box = {"groq": [], "whisper": [], "groq_status": 200, "retry_after": "2"}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.groq.com":
            assert request.url.path == "/openai/v1/audio/transcriptions"
            assert request.headers["Authorization"] == "Bearer gsk-test"
            body = request.content
            assert b'name="model"' in body and b"whisper-large-v3-turbo" in body
            assert b"verbose_json" in body and b"audio/wav" in body
            box["groq"].append(request)
            status = box["groq_status"]
            if callable(status):
                status = status(len(box["groq"]))
            if status == 429:
                return httpx.Response(429, headers={"retry-after": box["retry_after"]})
            if status != 200:
                return httpx.Response(status, json={"error": {"message": "secret detail"}})
            return httpx.Response(200, json={"text": "x", "segments": [seg(" From Groq.")]})
        assert request.url.path == "/transcribe"
        box["whisper"].append(request)
        if box.get("whisper_down"):
            return httpx.Response(500)
        return httpx.Response(200, json={"text": "x", "segments": [seg(" From Whisper.")]})

    http.set_transport(httpx.MockTransport(handler))
    yield box
    http.set_transport(None)


class Clock:
    def __init__(self):
        self.now = 1000.0
        self.slept: list[float] = []

    def __call__(self):
        return self.now

    async def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


def make_stt(clock, per_minute=18, use_groq=True):
    return SpeechToText(
        groq.transcribe if use_groq else None,
        whisper.transcribe,
        RateLimiter(per_minute, clock=clock),
        clock=clock,
        sleep=clock.sleep,
    )


WAV = to_wav(tone(-20) * 50)


def test_groq_is_used_when_it_answers(speech_services):
    clock = Clock()
    stt = make_stt(clock)
    heard = asyncio.run(stt.transcribe(WAV, "Earlier context."))
    assert heard == Heard("From Groq.", False, "groq")
    assert b"Earlier context." in speech_services["groq"][0].content
    assert speech_services["whisper"] == []
    assert stt.describe()["state"] == "running"


def test_a_short_rate_limit_is_waited_out(speech_services):
    speech_services["groq_status"] = lambda n: 429 if n == 1 else 200
    clock = Clock()
    heard = asyncio.run(make_stt(clock).transcribe(WAV))
    assert heard.provider == "groq" and clock.slept == [2.0]


def test_a_long_rate_limit_hands_over_to_whisper_until_it_passes(speech_services):
    speech_services["groq_status"] = 429
    speech_services["retry_after"] = "45"
    clock = Clock()
    stt = make_stt(clock)
    assert asyncio.run(stt.transcribe(WAV)).provider == "whisper"
    assert stt.describe()["state"] == "degraded"
    # while groq cools down it isn't asked at all
    asyncio.run(stt.transcribe(WAV))
    assert len(speech_services["groq"]) == 1 and len(speech_services["whisper"]) == 2
    clock.now += 46
    speech_services["groq_status"] = 200
    assert asyncio.run(stt.transcribe(WAV)).provider == "groq"
    assert stt.describe()["state"] == "running"


def test_a_server_error_is_retried_once_then_whisper_takes_over(speech_services):
    speech_services["groq_status"] = 503
    clock = Clock()
    assert asyncio.run(make_stt(clock).transcribe(WAV)).provider == "whisper"
    assert len(speech_services["groq"]) == 2 and clock.slept == [1.0]


def test_a_refused_key_is_not_retried(speech_services):
    speech_services["groq_status"] = 401
    clock = Clock()
    stt = make_stt(clock)
    assert asyncio.run(stt.transcribe(WAV)).provider == "whisper"
    assert len(speech_services["groq"]) == 1 and clock.slept == []
    assert stt.describe()["state"] == "degraded"


def test_without_a_groq_key_whisper_does_it_all(speech_services):
    clock = Clock()
    stt = make_stt(clock, use_groq=False)
    assert asyncio.run(stt.transcribe(WAV)).provider == "whisper"
    assert speech_services["groq"] == []
    assert "not set up" in stt.describe()["message"]


def test_both_services_down_is_reported(speech_services):
    speech_services["groq_status"] = 500
    speech_services["whisper_down"] = True
    with pytest.raises(SpeechServiceDown):
        asyncio.run(make_stt(Clock()).transcribe(WAV))


def test_the_rate_limiter_spaces_requests_within_a_minute():
    clock = Clock()

    async def go():
        limiter = RateLimiter(3, clock=clock)
        import app.realtime.stt as stt_module

        real_sleep = stt_module.asyncio.sleep
        stt_module.asyncio.sleep = clock.sleep
        try:
            for _ in range(4):
                await limiter.acquire()
        finally:
            stt_module.asyncio.sleep = real_sleep

    asyncio.run(go())
    assert clock.slept == [60.0]


# --- the room's pipeline -----------------------------------------------------------------


def utt(start_ms: int, ms: int = 1000) -> Utterance:
    start = T0 + timedelta(milliseconds=start_ms)
    return Utterance(tone(-20) * (ms // FRAME_MS), start, start + timedelta(milliseconds=ms), ms)


class FakeStt:
    def __init__(self, answers):
        self.answers = list(answers)
        self.calls: list[tuple[int, str | None]] = []

    async def transcribe(self, wav, prompt=None):
        self.calls.append((len(wav), prompt))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def run_pipeline(stt, items, save=None):
    saved: list[tuple] = []
    pushed: list[dict] = []

    def default_save(role, text, started_at, ended_at, uncertain, provider):
        saved.append((role, text, started_at, uncertain, provider))
        return {"sequence": len(saved), "text": text}

    async def publish(payload):
        pushed.append(payload)

    async def no_sleep(_):
        return None

    async def go():
        pipeline = TranscriptPipeline(stt, save or default_save, publish, sleep=no_sleep)
        for identity, role, u in items:
            pipeline.put(identity, role, u)
        pipeline.close()
        await pipeline.run()

    asyncio.run(go())
    return saved, pushed


def test_overlapping_speakers_each_get_their_own_turn():
    stt = FakeStt(
        [
            Heard("So tell me about it.", False, "groq"),
            Heard("Sure, I", False, "groq"),
        ]
    )
    saved, pushed = run_pipeline(
        stt,
        [
            ("interviewer-1", SpeakerRole.INTERVIEWER, utt(0, 2000)),
            ("candidate-4", SpeakerRole.CANDIDATE, utt(1500, 1000)),
        ],
    )
    assert [(s[0], s[1]) for s in saved] == [
        (SpeakerRole.INTERVIEWER, "So tell me about it."),
        (SpeakerRole.CANDIDATE, "Sure, I"),
    ]
    assert [p["sequence"] for p in pushed] == [1, 2]
    # the last thing said goes along as context for the next one
    assert stt.calls[1][1] == "So tell me about it."


def test_queued_pieces_from_one_speaker_go_out_as_one_request():
    stt = FakeStt([Heard("One long answer.", False, "groq"), Heard("Okay.", False, "groq")])
    saved, _ = run_pipeline(
        stt,
        [
            ("candidate-4", SpeakerRole.CANDIDATE, utt(0, 1000)),
            ("candidate-4", SpeakerRole.CANDIDATE, utt(1500, 1000)),
            ("interviewer-1", SpeakerRole.INTERVIEWER, utt(3000, 500)),
        ],
    )
    assert len(stt.calls) == 2
    assert saved[0][1] == "One long answer." and saved[0][2] == T0
    assert saved[1][0] == SpeakerRole.INTERVIEWER


def test_nothing_heard_saves_nothing_and_a_dead_service_leaves_a_marked_gap():
    stt = FakeStt([None, SpeechServiceDown()])
    saved, pushed = run_pipeline(
        stt,
        [
            ("candidate-4", SpeakerRole.CANDIDATE, utt(0)),
            ("interviewer-1", SpeakerRole.INTERVIEWER, utt(9000)),
        ],
    )
    assert saved == [(SpeakerRole.INTERVIEWER, UNHEARD, T0 + timedelta(seconds=9), True, "none")]
    assert len(pushed) == 1


def test_a_failed_save_is_retried():
    attempts = []

    def flaky(role, text, *rest):
        attempts.append(text)
        if len(attempts) < 2:
            raise RuntimeError("database restarting")
        return {"sequence": 1}

    stt = FakeStt([Heard("Hello.", False, "groq")])
    _, pushed = run_pipeline(stt, [("candidate-4", SpeakerRole.CANDIDATE, utt(0))], save=flaky)
    assert attempts == ["Hello.", "Hello."] and pushed == [{"sequence": 1}]


# --- saving turns ----------------------------------------------------------------------


def test_turns_get_offsets_from_the_session_start_and_rising_sequence(db_session):
    from app.models.candidate import Candidate
    from app.models.job import Job
    from app.services.interview_sessions import save_turn

    job, candidate = Job(title="Engineer"), Candidate(full_name="Grace Hopper")
    db_session.add_all([job, candidate])
    db_session.flush()
    interview = Interview(
        job_id=job.id,
        candidate_id=candidate.id,
        status=InterviewStatus.IN_PROGRESS,
        started_at=T0,
        join_token="x" * 64,
    )
    db_session.add(interview)
    db_session.commit()

    first = save_turn(
        db_session, interview.id, SpeakerRole.INTERVIEWER, "Welcome.",
        T0 + timedelta(seconds=5), T0 + timedelta(seconds=6), False, "groq",
    )  # fmt: skip
    second = save_turn(
        db_session, interview.id, SpeakerRole.CANDIDATE, "Thanks.",
        T0 + timedelta(seconds=7, milliseconds=250), T0 + timedelta(seconds=8), True, "whisper",
    )  # fmt: skip
    assert (first["sequence"], first["start_offset_ms"], first["end_offset_ms"]) == (1, 5000, 6000)
    assert second["sequence"] == 2 and second["start_offset_ms"] == 7250
    assert second["speaker_label"] == "Candidate" and second["is_uncertain"] is True
    assert second["spoken_at"].startswith("2026-10-16T09:00:07")
    row = db_session.query(TranscriptTurn).filter_by(sequence=2).one()
    assert row.stt_provider == "whisper"
