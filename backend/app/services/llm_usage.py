# token totals for the system status page. reads stored rows only, so it never calls the ai

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.analysis import AgentRun
from app.models.usage import LlmCall

RECENT_RUNS = 20


def summary(db: Session, days: int) -> dict[str, Any]:
    since = datetime.now(UTC) - timedelta(days=days)
    sums = (
        func.count(LlmCall.id),
        func.coalesce(func.sum(LlmCall.prompt_tokens), 0),
        func.coalesce(func.sum(LlmCall.completion_tokens), 0),
        func.coalesce(func.sum(LlmCall.total_tokens), 0),
    )
    recent = LlmCall.created_at >= since

    calls, prompt, completion, total = db.execute(select(*sums).where(recent)).one()

    def grouped(column) -> list[dict[str, Any]]:
        rows = db.execute(
            select(column, *sums).where(recent).group_by(column).order_by(sums[3].desc())
        ).all()
        return [
            {
                "name": name,
                "calls": c,
                "prompt_tokens": p,
                "completion_tokens": o,
                "total_tokens": t,
            }
            for name, c, p, o, t in rows
        ]

    runs = db.scalars(select(AgentRun).order_by(AgentRun.id.desc()).limit(RECENT_RUNS)).all()
    return {
        "days": days,
        "calls": calls,
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
        "by_model": grouped(LlmCall.model),
        "by_source": grouped(LlmCall.source),
        "recent_runs": [_run(r) for r in runs],
    }


def _run(run: AgentRun) -> dict[str, Any]:
    tokens = (run.context or {}).get("tokens") or {}
    return {
        "id": run.id,
        "graph_name": run.graph_name,
        "status": run.status.value,
        "started_at": run.started_at,
        "duration_ms": run.duration_ms,
        "calls": tokens.get("calls"),
        "total_tokens": tokens.get("total_tokens"),
    }
