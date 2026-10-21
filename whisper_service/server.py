# thin http wrapper around faster-whisper, in its own container so the model
# weights and cpu load stay out of the api.
#   GET  /health
#   POST /transcribe  audio file -> {"text": ..., "segments": [...]}

import os
import tempfile
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from faster_whisper import WhisperModel

MODEL_SIZE = os.getenv("WHISPER_MODEL_SIZE", "base.en")
COMPUTE_TYPE = os.getenv("WHISPER_COMPUTE_TYPE", "int8")
DEVICE = os.getenv("WHISPER_DEVICE", "cpu")
MAX_AUDIO_BYTES = int(os.getenv("WHISPER_MAX_AUDIO_BYTES", str(25 * 1024 * 1024)))

# loaded once at startup, takes a few seconds
_model: WhisperModel | None = None


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global _model
    print(f"[whisper] loading model={MODEL_SIZE} device={DEVICE} compute={COMPUTE_TYPE}")
    _model = WhisperModel(MODEL_SIZE, device=DEVICE, compute_type=COMPUTE_TYPE)
    print("[whisper] model ready")
    yield
    _model = None


app = FastAPI(title="IntelliHire Whisper Service", version="0.1.0", lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok" if _model is not None else "loading",
        "model": MODEL_SIZE,
        "device": DEVICE,
    }


@app.post("/transcribe")
async def transcribe(
    audio: UploadFile = File(..., description="Audio chunk (webm, wav, ogg or mp3)"),
    language: str = Form("en"),
) -> dict[str, Any]:
    # segment offsets are relative to this chunk, the backend adds the chunk start
    if _model is None:
        raise HTTPException(status_code=503, detail="Model is still loading")

    payload = await audio.read()
    if not payload:
        raise HTTPException(status_code=400, detail="Empty audio payload")
    if len(payload) > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="Audio chunk too large")

    # faster-whisper needs a path. the temp file is deleted right after,
    # raw audio is never kept
    suffix = os.path.splitext(audio.filename or "")[1] or ".webm"
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(payload)
            tmp_path = tmp.name

        segments, info = _model.transcribe(
            tmp_path,
            language=language or None,
            vad_filter=True,  # drop silence, so pauses do not become hallucinated text
            beam_size=1,      # greedy: materially faster on CPU, negligible quality cost here
        )

        collected = [
            {
                "start": round(segment.start, 3),
                "end": round(segment.end, 3),
                "text": segment.text.strip(),
                # same confidence fields groq returns, so the backend cleans both alike
                "avg_logprob": round(segment.avg_logprob, 3),
                "no_speech_prob": round(segment.no_speech_prob, 3),
                "compression_ratio": round(segment.compression_ratio, 3),
            }
            for segment in segments
            if segment.text.strip()
        ]
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - the backend only needs a clean failure
        print(f"[whisper] transcription failed: {type(exc).__name__}")
        raise HTTPException(status_code=500, detail="Transcription failed") from exc
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)

    return {
        "text": " ".join(segment["text"] for segment in collected),
        "segments": collected,
        "language": getattr(info, "language", language),
        "duration": round(getattr(info, "duration", 0.0), 3),
    }
