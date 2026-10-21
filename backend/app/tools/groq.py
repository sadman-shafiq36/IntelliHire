# groq's hosted whisper. one short wav per request; the audio is not kept anywhere

from typing import Any

import httpx

from app.config import settings
from app.tools import http

TIMEOUT = httpx.Timeout(20.0, connect=5.0)


class RateLimited(http.ProviderError):
    def __init__(self, retry_after: float) -> None:
        super().__init__("Groq is rate limiting us.", status=429, provider="groq")
        self.retry_after = retry_after


def configured() -> bool:
    return bool(settings.groq_api_key)


def transcribe(wav: bytes, prompt: str | None = None) -> dict[str, Any]:
    data = {
        "model": settings.groq_stt_model,
        "response_format": "verbose_json",
        "language": settings.transcription_language,
        "temperature": "0",
    }
    if prompt:
        data["prompt"] = prompt
    with http.client() as c:
        response = c.post(
            f"{settings.groq_base_url.rstrip('/')}/audio/transcriptions",
            headers={"Authorization": f"Bearer {settings.groq_api_key}"},
            data=data,
            files={"file": ("speech.wav", wav, "audio/wav")},
            timeout=TIMEOUT,
        )
    if response.status_code == 429:
        try:
            wait = float(response.headers.get("retry-after", "10"))
        except ValueError:
            wait = 10.0
        raise RateLimited(wait)
    return http.check(response, "groq", "transcribe speech").json()
