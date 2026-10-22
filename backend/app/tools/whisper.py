# the local faster-whisper container, used when groq is unavailable or not set up

from typing import Any

import httpx

from app.config import settings
from app.tools import http

# cpu transcription runs at about half realtime, so a 15 s chunk needs room
TIMEOUT = httpx.Timeout(45.0, connect=5.0)


def transcribe(wav: bytes) -> dict[str, Any]:
    with http.client() as c:
        response = c.post(
            f"{settings.whisper_service_url.rstrip('/')}/transcribe",
            data={"language": settings.transcription_language},
            files={"audio": ("speech.wav", wav, "audio/wav")},
            timeout=TIMEOUT,
        )
    return http.check(response, "whisper", "transcribe speech").json()
