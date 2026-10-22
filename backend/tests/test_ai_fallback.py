import json
from types import SimpleNamespace
from typing import Literal

import httpx
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


def _streaming_client(handler):
    return SimpleNamespace(models=SimpleNamespace(generate_content_stream=handler))


def _pieces(*texts):
    return [SimpleNamespace(text=t) for t in texts]


def test_stream_falls_back_before_any_text_was_sent(monkeypatch, real_mode):
    def handler(model, contents, config):
        if model == "primary":
            raise _unavailable()
        return iter(_pieces("Hello ", "there."))

    monkeypatch.setattr(ai, "_client", lambda: _streaming_client(handler))
    assert list(ai.stream_text("hi")) == [("backup", "Hello "), ("backup", "there.")]


def test_stream_failing_midway_is_not_hidden(monkeypatch, real_mode):
    def handler(model, contents, config):
        def gen():
            yield SimpleNamespace(text="Half an ")
            raise _unavailable()

        return gen()

    monkeypatch.setattr(ai, "_client", lambda: _streaming_client(handler))
    got = []
    with pytest.raises(ExternalServiceError):
        for item in ai.stream_text("hi"):
            got.append(item)
    # no second model starts the answer again after text was sent
    assert got == [("primary", "Half an ")]


def test_quick_stream_drops_minimal_thinking_for_a_model_without_it(monkeypatch, real_mode):
    configs = []

    def handler(model, contents, config):
        configs.append(config.thinking_config)
        if config.thinking_config is not None:
            raise errors.ClientError(
                400,
                {"error": {"code": 400, "message": "Thinking level MINIMAL is not supported"}},
            )
        return iter(_pieces("ok"))

    monkeypatch.setattr(ai, "_client", lambda: _streaming_client(handler))
    assert list(ai.stream_text("hi", quick=True)) == [("primary", "ok")]
    assert configs[0].thinking_level.value.lower() == "minimal" and configs[1] is None


# --- groq first, gemini as the fallback --------------------------------------------------


class Pick(BaseModel):
    choice: Literal["a", "b"]
    note: str | None


@pytest.fixture
def groq_first(monkeypatch, real_mode):
    from app.tools import http

    monkeypatch.setattr(settings, "groq_api_key", "gsk-test")
    monkeypatch.setattr(settings, "llm_providers", "groq,gemini")
    monkeypatch.setattr(settings, "groq_llm_models", "openai/gpt-oss-120b")
    fake: dict = {"requests": [], "reply": None}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        fake["requests"].append(body)
        return fake["reply"](body)

    http.set_transport(httpx.MockTransport(handler))
    yield fake
    http.set_transport(None)


def _chat(content: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def test_groq_answers_first_with_a_strict_schema(monkeypatch, groq_first):
    groq_first["reply"] = lambda body: _chat('{"choice": "a", "note": null}')
    monkeypatch.setattr(ai, "_client", lambda: pytest.fail("gemini must not be asked"))
    answer, model = ai.generate_structured_with_model("pick", Pick, system="be brief")
    assert answer == Pick(choice="a", note=None) and model == "groq:openai/gpt-oss-120b"
    body = groq_first["requests"][0]
    schema = body["response_format"]["json_schema"]
    assert schema["strict"] is True and schema["name"] == "Pick"
    # strict mode: every property required, nothing extra, no defaults
    assert schema["schema"]["required"] == ["choice", "note"]
    assert schema["schema"]["additionalProperties"] is False
    assert body["messages"][0] == {"role": "system", "content": "be brief"}
    assert body["include_reasoning"] is False and body["reasoning_effort"] == "medium"


def test_a_rate_limited_groq_hands_over_to_gemini_and_cools_down(monkeypatch, groq_first):
    groq_first["reply"] = lambda body: httpx.Response(429, headers={"retry-after": "30"})
    monkeypatch.setattr(
        ai,
        "_client",
        lambda: _client_with(lambda **kw: SimpleNamespace(parsed=Answer(text="g"), text="")),
    )
    answer, model = ai.generate_structured_with_model("prompt", Answer)
    assert answer.text == "g" and model == "primary"
    # the next call skips groq while it cools down
    ai.generate_structured_with_model("prompt", Answer)
    assert len(groq_first["requests"]) == 1


def test_an_unusable_groq_answer_hands_over_to_gemini(monkeypatch, groq_first):
    groq_first["reply"] = lambda body: _chat("not json")
    monkeypatch.setattr(
        ai,
        "_client",
        lambda: _client_with(lambda **kw: SimpleNamespace(parsed=Answer(text="g"), text="")),
    )
    assert ai.generate_structured_with_model("prompt", Answer)[1] == "primary"


def test_without_a_groq_key_gemini_answers(monkeypatch, groq_first):
    monkeypatch.setattr(settings, "groq_api_key", "")
    monkeypatch.setattr(
        ai,
        "_client",
        lambda: _client_with(lambda **kw: SimpleNamespace(parsed=Answer(text="g"), text="")),
    )
    assert ai.generate_structured_with_model("prompt", Answer)[1] == "primary"
    assert groq_first["requests"] == []


def _sse(*pieces: str) -> httpx.Response:
    lines = ["data: " + json.dumps({"choices": [{"delta": {"content": p}}]}) for p in pieces] + [
        "data: [DONE]"
    ]
    return httpx.Response(200, content=("\n\n".join(lines) + "\n\n").encode())


def test_groq_streams_first(monkeypatch, groq_first):
    groq_first["reply"] = lambda body: _sse("Hello ", "there.")
    got = list(ai.stream_text("hi", quick=True))
    assert got == [("groq:openai/gpt-oss-120b", "Hello "), ("groq:openai/gpt-oss-120b", "there.")]
    body = groq_first["requests"][0]
    assert body["stream"] is True and body["reasoning_effort"] == "low"


def test_a_failing_groq_stream_hands_over_to_gemini(monkeypatch, groq_first):
    groq_first["reply"] = lambda body: httpx.Response(503)
    monkeypatch.setattr(
        ai,
        "_client",
        lambda: _streaming_client(lambda model, contents, config: iter(_pieces("ok"))),
    )
    assert list(ai.stream_text("hi")) == [("primary", "ok")]


def test_a_short_groq_wait_is_waited_out_on_the_same_model(monkeypatch, groq_first):
    slept = []
    monkeypatch.setattr(ai.time, "sleep", slept.append)
    replies = iter(
        [httpx.Response(429, headers={"retry-after": "4"}), _chat('{"choice": "b", "note": "x"}')]
    )
    groq_first["reply"] = lambda body: next(replies)
    answer, model = ai.generate_structured_with_model("pick", Pick)
    assert answer.choice == "b" and model == "groq:openai/gpt-oss-120b" and slept == [4.0]
