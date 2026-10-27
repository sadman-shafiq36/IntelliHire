# token counts from llm answers. each call is logged and saved as an llm_calls row, and
# inside track() the counts are also added up so the agent run can store its total.
# counts only, never text

import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from app.logging_config import get_logger

log = get_logger(__name__)


class Usage:
    def __init__(self, source: str | None = None) -> None:
        # langgraph runs parallel nodes on threads that share this object
        self._lock = threading.Lock()
        self.source = source
        self.calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0
        self.by_model: dict[str, int] = {}

    def add(self, model: str, prompt: int, completion: int, total: int) -> None:
        with self._lock:
            self.calls += 1
            self.prompt_tokens += prompt
            self.completion_tokens += completion
            self.total_tokens += total
            self.by_model[model] = self.by_model.get(model, 0) + total

    def as_dict(self) -> dict[str, Any] | None:
        if not self.calls:
            return None
        return {
            "calls": self.calls,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "by_model": dict(self.by_model),
        }


_current: ContextVar[Usage | None] = ContextVar("llm_usage", default=None)


@contextmanager
def track(source: str | None = None) -> Iterator[Usage]:
    usage = Usage(source)
    reset = _current.set(usage)
    try:
        yield usage
    finally:
        _current.reset(reset)


def _save(row: dict[str, Any]) -> None:
    # its own short session: callers can be worker threads with no session of their own
    from app.db.session import get_session_factory
    from app.models.usage import LlmCall

    with get_session_factory()() as db:
        db.add(LlmCall(**row))
        db.commit()


# tests swap this so nothing is written to a real database
sink: Callable[[dict[str, Any]], None] | None = _save


def _count(value: Any) -> int:
    return value if isinstance(value, int) and value > 0 else 0


def record(model: str, prompt: Any, completion: Any, total: Any = None) -> None:
    # completion includes reasoning tokens the model thought but didn't send back
    prompt, completion = _count(prompt), _count(completion)
    total = _count(total) or prompt + completion
    usage = _current.get()
    source = usage.source if usage else None
    log.info(
        "llm_usage",
        model=model,
        source=source,
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=total,
    )
    if usage is not None:
        usage.add(model, prompt, completion, total)
    if sink is not None:
        try:
            sink(
                {
                    "model": model[:80],
                    "source": source,
                    "prompt_tokens": prompt,
                    "completion_tokens": completion,
                    "total_tokens": total,
                }
            )
        except Exception as exc:
            # counting must never break the ai call it counts
            log.warning("llm_usage_not_saved", error_type=type(exc).__name__)
