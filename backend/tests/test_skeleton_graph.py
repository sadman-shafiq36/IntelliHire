from app.agents.skeleton import build_skeleton_graph


def test_happy_path_runs_every_node():
    graph = build_skeleton_graph()
    final = graph.invoke({"input_text": "hello interview"})

    assert final["validated"] is True
    assert final["transformed_text"] == "HELLO INTERVIEW"
    assert final["result"]["ok"] is True
    assert final["is_complete"] is True
    assert final["result"]["error_count"] == 0


def test_invalid_input_skips_the_work_but_still_finalises():
    # finalise still runs so the caller always gets a result
    graph = build_skeleton_graph()
    final = graph.invoke({"input_text": "   "})

    assert final["validated"] is False
    assert "transformed_text" not in final
    assert final["is_complete"] is True
    assert final["result"]["ok"] is False


def test_errors_accumulate_rather_than_overwrite():
    # operator.add reducer appends instead of replacing
    graph = build_skeleton_graph()
    final = graph.invoke({"input_text": ""})

    assert len(final["errors"]) == 1
    assert final["errors"][0]["code"] == "empty_input"
    assert final["errors"][0]["node"] == "validate"


def test_state_carries_input_through_to_the_end():
    graph = build_skeleton_graph()
    final = graph.invoke({"input_text": "keep me", "thread_id": "t-123"})

    assert final["input_text"] == "keep me"
    assert final["thread_id"] == "t-123"


def test_run_graph_stores_the_token_total(db_session):
    from app.agents.runner import run_graph
    from app.models.analysis import AgentRun
    from app.tools import usage

    def invoke():
        usage.record("groq:openai/gpt-oss-120b", 90, 10)
        return {"errors": []}

    run_graph(db_session, "demo", "demo-1", invoke, {"job_id": 3})
    run = db_session.query(AgentRun).one()
    assert run.context["job_id"] == 3
    assert run.context["tokens"]["total_tokens"] == 100
    assert run.context["tokens"]["calls"] == 1
