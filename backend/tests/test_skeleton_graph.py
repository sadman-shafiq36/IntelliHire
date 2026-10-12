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
