# every llm call goes through here. text comes from groq first (gpt-oss) and falls back
# to gemini; embeddings are gemini only, since vectors from two models can't be mixed.
# with USE_FAKE_AI=true the fakes below are used instead, so tests never touch the network

import hashlib
import math
import re
import threading
import time
from collections.abc import Iterator
from functools import partial
from typing import Any, TypeVar

from pydantic import BaseModel
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from app.config import settings
from app.errors import ExternalServiceError
from app.logging_config import get_logger
from app.tools import groq, usage

log = get_logger(__name__)

SchemaT = TypeVar("SchemaT", bound=BaseModel)

# gemini accepts at most 100 texts per embed request
EMBED_BATCH_SIZE = 100


# one client for the whole process. built under a lock: when several threads raced to
# build it, the spare clients were thrown away and closed while still in use
_client_lock = threading.Lock()
_client_instance: Any = None


def _client():
    global _client_instance
    if _client_instance is None:
        with _client_lock:
            if _client_instance is None:
                from google import genai

                if not settings.google_api_key:
                    raise ExternalServiceError(
                        "The AI service is not configured. Add GOOGLE_API_KEY to .env."
                    )
                from google.genai import types

                _client_instance = genai.Client(
                    api_key=settings.google_api_key,
                    # without a timeout one stalled connection hung a batch for 12 hours
                    http_options=types.HttpOptions(timeout=settings.llm_timeout_seconds * 1000),
                )
    return _client_instance


# a model that answered 429 is skipped until its cool-down ends, so a batch doesn't
# ask a rate-limited or spent model again on every call
_cooldown_lock = threading.Lock()
_cooldown_until: dict[str, float] = {}
DEFAULT_COOLDOWN = 60.0
MAX_COOLDOWN = 3600.0


def _retry_delay(exc: BaseException) -> float:
    after = getattr(exc, "retry_after", None)
    if after:
        return min(MAX_COOLDOWN, max(1.0, float(after)))
    details = getattr(exc, "details", None) or {}
    for item in (details.get("error") or {}).get("details", []) or []:
        delay = str(item.get("retryDelay") or "")
        if delay.endswith("s"):
            try:
                return min(MAX_COOLDOWN, max(1.0, float(delay[:-1])))
            except ValueError:
                pass
    return DEFAULT_COOLDOWN


def _cool_down(model: str, seconds: float) -> None:
    with _cooldown_lock:
        _cooldown_until[model] = time.monotonic() + seconds
    log.info("llm_model_cooling_down", model=model, seconds=round(seconds))


def _available(models: list[str]) -> list[str]:
    now = time.monotonic()
    with _cooldown_lock:
        ready = [m for m in models if _cooldown_until.get(m, 0) <= now]
    # if everything is cooling down, try them all rather than fail without asking
    return ready or models


def _chain() -> list[tuple[str, str]]:
    # (provider, model) in the order they are asked. groq is skipped without a key
    chain: list[tuple[str, str]] = []
    for provider in settings.llm_provider_list:
        if provider == "groq" and settings.groq_api_key:
            chain += [("groq", m) for m in settings.groq_llm_model_list]
        elif provider == "gemini":
            chain += [
                ("gemini", m) for m in [settings.gemini_model, *settings.gemini_fallback_model_list]
            ]
    return chain


def _name(provider: str, model: str) -> str:
    # what is stored as the model that answered; gemini names stay as they were
    return f"groq:{model}" if provider == "groq" else model


def _ready_chain() -> list[tuple[str, str]]:
    chain = _chain()
    ready = set(_available([_name(p, m) for p, m in chain]))
    return [(p, m) for p, m in chain if _name(p, m) in ready]


def seconds_until_available() -> float:
    names = [_name(p, m) for p, m in _chain()]
    if not names:
        return 0.0
    now = time.monotonic()
    with _cooldown_lock:
        return max(0.0, min(_cooldown_until.get(n, 0) - now for n in names))


def _is_transient(exc: BaseException) -> bool:
    from google.genai import errors

    if isinstance(exc, errors.ServerError):
        return True
    return isinstance(exc, errors.ClientError) and getattr(exc, "code", None) == 429


def _is_server_error(exc: BaseException) -> bool:
    from google.genai import errors

    return isinstance(exc, errors.ServerError)


def _with_retries(fn, retry_on=_is_transient):
    return retry(
        retry=retry_if_exception(retry_on),
        stop=stop_after_attempt(max(1, settings.llm_max_retries)),
        wait=wait_exponential(multiplier=1, min=1, max=20),
        reraise=True,
    )(fn)


def embed_texts(texts: list[str], *, for_query: bool = False) -> list[list[float]]:
    if not texts:
        return []
    if settings.use_fake_ai:
        return [_fake_embedding(text) for text in texts]

    from google.genai import types

    config = types.EmbedContentConfig(
        task_type="RETRIEVAL_QUERY" if for_query else "RETRIEVAL_DOCUMENT",
        output_dimensionality=settings.embedding_dimensions,
    )

    @_with_retries
    def _call(batch: list[str]):
        return _client().models.embed_content(
            model=settings.gemini_embedding_model, contents=batch, config=config
        )

    vectors: list[list[float]] = []
    for start in range(0, len(texts), EMBED_BATCH_SIZE):
        batch = texts[start : start + EMBED_BATCH_SIZE]
        try:
            response = _call(batch)
        except ExternalServiceError:
            raise
        except Exception as exc:
            log.warning("embedding_failed", error_type=type(exc).__name__, batch_size=len(batch))
            raise ExternalServiceError("The AI service could not process the text.") from exc
        vectors.extend(list(item.values) for item in response.embeddings)
    return vectors


def generate_structured(prompt: str, schema: type[SchemaT], *, system: str = "") -> SchemaT:
    return generate_structured_with_model(prompt, schema, system=system)[0]


def generate_structured_with_model(
    prompt: str, schema: type[SchemaT], *, system: str = ""
) -> tuple[SchemaT, str]:
    # also says which model answered, since a fallback may have been used
    if settings.use_fake_ai:
        raise RuntimeError("generate_structured has no generic fake, inject one in the caller")

    chain = _ready_chain()
    for index, (provider, model) in enumerate(chain):
        name = _name(provider, model)
        try:
            if provider == "groq":
                answer = _after_short_wait(
                    partial(
                        groq.chat_json,
                        model,
                        prompt,
                        schema.__name__,
                        schema.model_json_schema(),
                        system,
                    )
                )
                return schema.model_validate_json(answer), name
            return _gemini_structured(model, prompt, schema, system), name
        except ExternalServiceError:
            raise
        except Exception as exc:
            _note_failure("llm_call_failed", name, exc)
            # any groq failure hands over to gemini; a gemini error that isn't about load
            # (a bad request or key) won't be fixed by asking another gemini model
            if index == len(chain) - 1 or (provider == "gemini" and not _gemini_may_recover(exc)):
                raise ExternalServiceError(_BUSY) from exc
    raise ExternalServiceError("No AI model is configured. Add GROQ_API_KEY or GOOGLE_API_KEY.")


_BUSY = "The AI service is busy or unavailable right now. Please try again shortly."

# groq's free tier counts tokens per minute and says how long until there are enough again;
# a wait this short is better than handing a live request to a busier model
GROQ_MAX_WAIT = 15.0


def _short_wait(exc: BaseException) -> float | None:
    after = getattr(exc, "retry_after", None)
    if getattr(exc, "code", None) == 429 and after and after <= GROQ_MAX_WAIT:
        return after
    return None


def _after_short_wait(call: Any) -> Any:
    try:
        return call()
    except groq.ChatFailed as exc:
        wait = _short_wait(exc)
        if wait is None:
            raise
        time.sleep(wait)
        return call()


def _groq_stream(model: str, prompt: str, system: str, quick: bool) -> Iterator[str]:
    # chat_stream only fails before its first piece, so asking again can't repeat text
    try:
        yield from groq.chat_stream(model, prompt, system, quick)
    except groq.ChatFailed as exc:
        wait = _short_wait(exc)
        if wait is None:
            raise
        time.sleep(wait)
        yield from groq.chat_stream(model, prompt, system, quick)


class _InvalidAnswer(Exception):
    # the model answered, but not with the json asked for
    pass


def _note_failure(event: str, name: str, exc: BaseException, **extra: Any) -> None:
    status = getattr(exc, "code", None)
    log.warning(event, error_type=type(exc).__name__, status=status, model=name, **extra)
    if status == 429:
        _cool_down(name, _retry_delay(exc))


def _gemini_may_recover(exc: BaseException) -> bool:
    return isinstance(exc, _InvalidAnswer) or _is_transient(exc)


def _gemini_structured(model: str, prompt: str, schema: type[SchemaT], system: str) -> SchemaT:
    from google.genai import types

    config = types.GenerateContentConfig(
        system_instruction=system or None,
        temperature=settings.llm_temperature,
        response_mime_type="application/json",
        response_schema=schema,
    )

    # only overload (5xx) is retried on the same model. a 429 is usually a spent
    # quota (the free tier is 20 requests a day per model), so move on straight away
    @partial(_with_retries, retry_on=_is_server_error)
    def _call():
        return _client().models.generate_content(model=model, contents=prompt, config=config)

    response = _call()
    _gemini_usage(model, response)
    parsed = response.parsed
    if isinstance(parsed, schema):
        return parsed
    # parsed is None when the reply didn't match the schema, try the raw text once
    try:
        return schema.model_validate_json(response.text or "")
    except Exception as exc:
        raise _InvalidAnswer() from exc


def _gemini_usage(model: str, response: Any) -> None:
    meta = getattr(response, "usage_metadata", None)
    if meta is None:
        return
    # thinking tokens are billed like answer tokens, so they count as completion
    completion = (getattr(meta, "candidates_token_count", 0) or 0) + (
        getattr(meta, "thoughts_token_count", 0) or 0
    )
    usage.record(
        model,
        getattr(meta, "prompt_token_count", None),
        completion,
        getattr(meta, "total_token_count", None),
    )


def _rejects_thinking_level(exc: BaseException) -> bool:
    return getattr(exc, "code", None) == 400 and "thinking level" in str(exc).lower()


def _gemini_stream(model: str, prompt: str, system: str, quick: bool) -> Iterator[str]:
    from google.genai import types

    def config(thinking: bool) -> Any:
        return types.GenerateContentConfig(
            system_instruction=system or None,
            temperature=settings.llm_temperature,
            thinking_config=types.ThinkingConfig(thinking_level="minimal") if thinking else None,
        )

    minimal = quick
    started = False
    while True:
        try:
            last = None
            for chunk in _client().models.generate_content_stream(
                model=model, contents=prompt, config=config(minimal)
            ):
                last = chunk
                if chunk.text:
                    started = True
                    yield chunk.text
            # each chunk carries the running counts; the last one has the totals
            if last is not None:
                _gemini_usage(model, last)
            return
        except Exception as exc:
            # not every model has a minimal level; ask that one again without it
            if minimal and not started and _rejects_thinking_level(exc):
                minimal = False
                continue
            raise


def stream_text(prompt: str, *, system: str = "", quick: bool = False) -> Iterator[tuple[str, str]]:
    # yields (model, piece of text) as it is written. a model that fails before its first
    # piece is skipped for the next one; a failure after text was sent can't be hidden.
    # quick asks for the least thinking: on gemini, 1-5 s to the first words instead of 10-26
    if settings.use_fake_ai:
        raise RuntimeError("stream_text has no fake, inject one in the caller")

    chain = _ready_chain()
    for index, (provider, model) in enumerate(chain):
        name = _name(provider, model)
        started = False
        pieces = (
            _groq_stream(model, prompt, system, quick)
            if provider == "groq"
            else _gemini_stream(model, prompt, system, quick)
        )
        try:
            for piece in pieces:
                started = True
                yield name, piece
            return
        except ExternalServiceError:
            raise
        except Exception as exc:
            _note_failure("llm_stream_failed", name, exc, started=started)
            if (
                started
                or index == len(chain) - 1
                or (provider == "gemini" and not _gemini_may_recover(exc))
            ):
                raise ExternalServiceError(_BUSY) from exc
    raise ExternalServiceError("No AI model is configured. Add GROQ_API_KEY or GOOGLE_API_KEY.")


# --- fakes -------------------------------------------------------------------

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _fake_embedding(text: str, dims: int = 256) -> list[float]:
    # hashed bag of words: texts sharing words end up close, good enough for tests
    vector = [0.0] * dims
    for token in _TOKEN_RE.findall(text.lower()):
        digest = hashlib.md5(token.encode()).digest()
        index = int.from_bytes(digest[:4], "little") % dims
        vector[index] += 1.0 if digest[4] % 2 else -1.0
    norm = math.sqrt(sum(v * v for v in vector)) or 1.0
    return [v / norm for v in vector]
