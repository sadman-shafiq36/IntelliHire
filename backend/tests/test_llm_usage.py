from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.agents.runner import run_graph
from app.models.analysis import AgentRun
from app.models.enums import AgentRunStatus
from app.models.usage import LlmCall
from app.tools import usage


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_each_call_is_saved_with_the_graph_that_made_it(db_session, llm_calls_saved):
    def invoke():
        usage.record("groq:openai/gpt-oss-120b", 90, 10)
        return {"errors": []}

    run_graph(db_session, "jd_generation", "jd-1", invoke, {"job_id": 3})
    usage.record("gemini-3.5-flash", 5, 5)
    assert llm_calls_saved == [
        {
            "model": "groq:openai/gpt-oss-120b",
            "source": "jd_generation",
            "prompt_tokens": 90,
            "completion_tokens": 10,
            "total_tokens": 100,
        },
        {
            "model": "gemini-3.5-flash",
            "source": None,
            "prompt_tokens": 5,
            "completion_tokens": 5,
            "total_tokens": 10,
        },
    ]


def test_a_failing_sink_never_breaks_the_call(monkeypatch):
    def broken(row):
        raise RuntimeError("database down")

    monkeypatch.setattr(usage, "sink", broken)
    usage.record("groq:x", 1, 1)


def test_usage_summary_adds_up_the_period(client: TestClient, interviewer_token: str, db_session):
    now = datetime.now(UTC)
    db_session.add_all(
        [
            LlmCall(model="groq:a", source="cv_screening", prompt_tokens=100,
                    completion_tokens=20, total_tokens=120, created_at=now),
            LlmCall(model="groq:a", source="cv_screening", prompt_tokens=50,
                    completion_tokens=10, total_tokens=60, created_at=now),
            LlmCall(model="gemini-b", source=None, prompt_tokens=10,
                    completion_tokens=10, total_tokens=20, created_at=now),
            # older than a day: only in the week
            LlmCall(model="groq:a", source="cv_screening", prompt_tokens=1000,
                    completion_tokens=0, total_tokens=1000, created_at=now - timedelta(days=3)),
            AgentRun(graph_name="cv_screening", thread_id="s-1", status=AgentRunStatus.SUCCEEDED,
                     started_at=now, duration_ms=900,
                     context={"tokens": {"calls": 2, "total_tokens": 180}}),
        ]
    )  # fmt: skip
    db_session.commit()

    day = client.get("/api/v1/usage", headers=_auth(interviewer_token)).json()
    assert (day["calls"], day["total_tokens"], day["prompt_tokens"]) == (3, 200, 160)
    assert day["by_model"][0] == {
        "name": "groq:a",
        "calls": 2,
        "prompt_tokens": 150,
        "completion_tokens": 30,
        "total_tokens": 180,
    }
    assert [g["name"] for g in day["by_source"]] == ["cv_screening", None]
    assert day["recent_runs"][0]["total_tokens"] == 180

    week = client.get("/api/v1/usage?days=7", headers=_auth(interviewer_token)).json()
    assert week["total_tokens"] == 1200


def test_usage_is_for_interviewers_only(client: TestClient, candidate_token: str):
    response = client.get("/api/v1/usage", headers=_auth(candidate_token))
    assert response.status_code == 403
