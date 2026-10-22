# groq's hosted whisper. one short wav per request; the audio is not kept anywhere

import json
from collections.abc import Iterator
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


# --- chat models, the default for every ai step (gemini is the fallback) ----------------


class ChatFailed(Exception):
    # status is None when the answer arrived but wasn't the json asked for
    def __init__(self, status: int | None, retry_after: float | None = None) -> None:
        super().__init__(f"groq chat failed ({status})")
        self.code = status
        self.retry_after = retry_after


def _retry_after(response: httpx.Response) -> float | None:
    try:
        return float(response.headers["retry-after"])
    except (KeyError, ValueError):
        return None


def _body(model: str, prompt: str, system: str, effort: str) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": model,
        "messages": ([{"role": "system", "content": system}] if system else [])
        + [{"role": "user", "content": prompt}],
        "temperature": settings.llm_temperature,
    }
    # the reasoning models think before answering; the thinking isn't wanted back
    if model.startswith("openai/gpt-oss"):
        body["reasoning_effort"] = effort
        body["include_reasoning"] = False
    return body


def _url() -> str:
    return f"{settings.groq_base_url.rstrip('/')}/chat/completions"


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {settings.groq_api_key}"}


def _timeout() -> httpx.Timeout:
    return httpx.Timeout(float(settings.llm_timeout_seconds), connect=10.0)


def strict_schema(node: Any) -> Any:
    # groq's strict mode wants every property required and no extra properties; an
    # optional field is already a nullable type in these schemas
    if isinstance(node, list):
        return [strict_schema(v) for v in node]
    if not isinstance(node, dict):
        return node
    out: dict[str, Any] = {}
    for key, value in node.items():
        if key == "default":
            continue
        if key in ("properties", "$defs"):
            out[key] = {name: strict_schema(sub) for name, sub in value.items()}
        else:
            out[key] = strict_schema(value)
    if out.get("type") == "object":
        out["required"] = list(out.get("properties", {}))
        out["additionalProperties"] = False
    return out


def chat_json(
    model: str, prompt: str, schema_name: str, schema: dict[str, Any], system: str = ""
) -> str:
    # returns the answer's text, json matching the schema; the caller validates it
    body = _body(model, prompt, system, "medium")
    body["response_format"] = {
        "type": "json_schema",
        "json_schema": {"name": schema_name, "strict": True, "schema": strict_schema(schema)},
    }
    with http.client() as c:
        response = c.post(_url(), headers=_headers(), json=body, timeout=_timeout())
    if response.status_code != 200:
        # the body can echo the prompt, so only the status goes anywhere
        raise ChatFailed(response.status_code, _retry_after(response))
    try:
        return response.json()["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, ValueError) as exc:
        raise ChatFailed(None) from exc


def chat_stream(model: str, prompt: str, system: str = "", quick: bool = False) -> Iterator[str]:
    # yields pieces of the answer as they arrive (server-sent events)
    body = _body(model, prompt, system, "low" if quick else "medium")
    body["stream"] = True
    with (
        http.client() as c,
        c.stream("POST", _url(), headers=_headers(), json=body, timeout=_timeout()) as response,
    ):
        if response.status_code != 200:
            response.read()
            raise ChatFailed(response.status_code, _retry_after(response))
        for line in response.iter_lines():
            if not line.startswith("data: "):
                continue
            data = line[6:].strip()
            if data == "[DONE]":
                return
            try:
                piece = json.loads(data)["choices"][0].get("delta", {}).get("content")
            except (KeyError, IndexError, ValueError):
                continue
            if piece:
                yield piece
