from types import SimpleNamespace

import pytest
from google.genai import errors
from pydantic import BaseModel

from app.config import settings
from app.errors import ExternalServiceError
from app.services import ai


class Answer(BaseModel):
    text: str


def _unavailable() -> errors.ServerError:
    return errors.ServerError(503, {"error": {"code": 503, "status": "UNAVAILABLE"}})


@pytest.fixture
def real_mode(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "use_fake_ai", False)
    monkeypatch.setattr(settings, "gemini_model", "primary")
    monkeypatch.setattr(settings, "gemini_fallback_models", "backup")
    monkeypatch.setattr(settings, "llm_max_retries", 2)
    monkeypatch.setattr(ai, "_cooldown_until", {})
    # no real sleeping between retries
    monkeypatch.setattr("tenacity.nap.time.sleep", lambda _: None)


def _client_with(handler):
    return SimpleNamespace(models=SimpleNamespace(generate_content=handler))


def test_falls_back_when_the_primary_stays_overloaded(monkeypatch, real_mode):
    calls = []

    def handler(model, contents, config):
        calls.append(model)
        if model == "primary":
            raise _unavailable()
        return SimpleNamespace(parsed=Answer(text="ok"), text="")

    monkeypatch.setattr(ai, "_client", lambda: _client_with(handler))
    assert ai.generate_structured("prompt", Answer).text == "ok"
    # primary retried, then the fallback answered first time
    assert calls == ["primary", "primary", "backup"]


def test_a_non_transient_error_does_not_fall_back(monkeypatch, real_mode):
    calls = []

    def handler(model, contents, config):
        calls.append(model)
        raise errors.ClientError(400, {"error": {"code": 400, "status": "INVALID_ARGUMENT"}})

    monkeypatch.setattr(ai, "_client", lambda: _client_with(handler))
    with pytest.raises(ExternalServiceError):
        ai.generate_structured("prompt", Answer)
    assert calls == ["primary"]


def test_gives_up_with_a_readable_message_when_everything_is_down(monkeypatch, real_mode):
    def handler(model, contents, config):
        raise _unavailable()

    monkeypatch.setattr(ai, "_client", lambda: _client_with(handler))
    with pytest.raises(ExternalServiceError, match="busy or unavailable"):
        ai.generate_structured("prompt", Answer)


def test_a_spent_quota_moves_on_without_retrying(monkeypatch, real_mode):
    calls = []

    def handler(model, contents, config):
        calls.append(model)
        if model == "primary":
            raise errors.ClientError(429, {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED"}})
        return SimpleNamespace(parsed=Answer(text="ok"), text="")

    monkeypatch.setattr(ai, "_client", lambda: _client_with(handler))
    assert ai.generate_structured("prompt", Answer).text == "ok"
    assert calls == ["primary", "backup"]


def test_a_rate_limited_model_is_skipped_until_its_cooldown_ends(monkeypatch, real_mode):
    monkeypatch.setattr(ai, "_cooldown_until", {})
    calls = []

    def handler(model, contents, config):
        calls.append(model)
        if model == "primary":
            raise errors.ClientError(
                429,
                {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED",
                           "details": [{"retryDelay": "30s"}]}},
            )  # fmt: skip
        return SimpleNamespace(parsed=Answer(text="ok"), text="")

    monkeypatch.setattr(ai, "_client", lambda: _client_with(handler))
    ai.generate_structured("one", Answer)
    ai.generate_structured("two", Answer)
    # the second call goes straight to the fallback
    assert calls == ["primary", "backup", "backup"]


def test_seconds_until_available_is_the_shortest_cooldown(monkeypatch, real_mode):
    now = ai.time.monotonic()
    monkeypatch.setattr(ai, "_cooldown_until", {})
    assert ai.seconds_until_available() == 0
    monkeypatch.setattr(ai, "_cooldown_until", {"primary": now + 3600, "backup": now + 40})
    assert 30 < ai.seconds_until_available() <= 40


def test_the_client_is_built_once_under_concurrency(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    built = []

    class FakeClient:
        def __init__(self, api_key, http_options=None):
            built.append(api_key)

    import google.genai

    monkeypatch.setattr(google.genai, "Client", FakeClient)
    monkeypatch.setattr(ai, "_client_instance", None)
    monkeypatch.setattr(settings, "google_api_key", "key")
    with ThreadPoolExecutor(8) as pool:
        clients = list(pool.map(lambda _: ai._client(), range(16)))
    assert len(built) == 1
    assert all(c is clients[0] for c in clients)


def test_the_client_has_a_request_timeout(monkeypatch):
    seen = {}

    class FakeClient:
        def __init__(self, api_key, http_options=None):
            seen["timeout"] = http_options.timeout

    import google.genai

    monkeypatch.setattr(google.genai, "Client", FakeClient)
    monkeypatch.setattr(ai, "_client_instance", None)
    monkeypatch.setattr(settings, "google_api_key", "key")
    monkeypatch.setattr(settings, "llm_timeout_seconds", 90)
    ai._client()
    assert seen["timeout"] == 90_000
