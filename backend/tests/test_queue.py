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


def test_auto_sync_is_queued_once_per_interval(monkeypatch: pytest.MonkeyPatch):
    from app.worker import run

    class FakeRedis:
        def __init__(self) -> None:
            self.keys: dict[str, str] = {}

        def set(self, key, value, nx=False, ex=None):
            if nx and key in self.keys:
                return None
            self.keys[key] = value
            return True

    queued = []

    class FakeQueue:
        def enqueue(self, func, **options):
            queued.append(func)

    redis = FakeRedis()
    monkeypatch.setattr(run, "get_redis", lambda: redis)
    monkeypatch.setattr(run, "get_queue", lambda: FakeQueue())

    assert run.queue_auto_sync(900) is True
    # a second worker, or this one waking again before the lock expires
    assert run.queue_auto_sync(900) is False
    assert queued == ["app.worker.tasks.auto_sync_applications_task"]
