import hashlib
import math
import re
from functools import lru_cache, partial
from typing import TypeVar

from pydantic import BaseModel
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from app.config import settings
from app.errors import ExternalServiceError
from app.logging_config import get_logger

log = get_logger(__name__)

SchemaT = TypeVar("SchemaT", bound=BaseModel)

# gemini accepts at most 100 texts per embed request
EMBED_BATCH_SIZE = 100


@lru_cache(maxsize=1)
def _client():
    from google import genai

    if not settings.google_api_key:
        raise ExternalServiceError("The AI service is not configured. Add GOOGLE_API_KEY to .env.")
    return genai.Client(api_key=settings.google_api_key)


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
    if settings.use_fake_ai:
        raise RuntimeError("generate_structured has no generic fake, inject one in the caller")

    from google.genai import types

    config = types.GenerateContentConfig(
        system_instruction=system or None,
        temperature=settings.llm_temperature,
        response_mime_type="application/json",
        response_schema=schema,
    )

    @partial(_with_retries, retry_on=_is_server_error)
    def _call(model: str):
        return _client().models.generate_content(model=model, contents=prompt, config=config)

    # when the primary model stays overloaded after its retries, try the fallbacks.
    # a non-transient error (bad request, auth) stops straight away
    models = [settings.gemini_model, *settings.gemini_fallback_model_list]
    response = None
    for index, model in enumerate(models):
        try:
            response = _call(model)
            break
        except ExternalServiceError:
            raise
        except Exception as exc:
            log.warning(
                "llm_call_failed",
                error_type=type(exc).__name__,
                status=getattr(exc, "code", None),
                model=model,
            )
            if not _is_transient(exc) or index == len(models) - 1:
                raise ExternalServiceError(
                    "The AI service is busy or unavailable right now. Please try again shortly."
                ) from exc
    assert response is not None

    parsed = response.parsed
    if isinstance(parsed, schema):
        return parsed
    # parsed is None when the reply didn't match the schema, try the raw text once
    try:
        return schema.model_validate_json(response.text or "")
    except Exception as exc:
        log.warning("llm_output_invalid", model=settings.gemini_model)
        raise ExternalServiceError("The AI service returned an unusable answer.") from exc



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
