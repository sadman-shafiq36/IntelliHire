from types import SimpleNamespace

import pytest

from app.worker import queue


def test_task_arguments_never_clash_with_rq_options(monkeypatch: pytest.MonkeyPatch):
    # rq treats job_id, timeout etc. as its own options unless passed inside kwargs
    calls = []

    class FakeQueue:
        def enqueue(self, func, *args, **options):
            calls.append((func, args, options))
            return SimpleNamespace(id="rq-1")

    monkeypatch.setattr(queue, "get_queue", lambda: FakeQueue())
    monkeypatch.setattr(queue, "report_progress", lambda *a, **k: None)

    assert queue.enqueue("app.worker.tasks.x", "kind", job_id=42, timeout=5) == "rq-1"
    func, args, options = calls[0]
    assert func == "app.worker.tasks.x"
    assert args == ()
    assert options == {"kwargs": {"job_id": 42, "timeout": 5}}
