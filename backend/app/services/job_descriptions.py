from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.checkpointer import thread_config
from app.agents.jd.draft import PROMPT_VERSION
from app.agents.jd.graph import MAX_ATTEMPTS
from app.agents.runner import new_thread_id, run_graph
from app.config import settings
from app.errors import AppError, ConflictError, ExternalServiceError, NotFoundError
from app.logging_config import get_logger
from app.models.enums import ChatMessageStatus, ChatRole, DescriptionSource
from app.models.job import Job, JobDescriptionMessage
from app.services.description_versions import record_version
from app.services.jobs import get_job

log = get_logger(__name__)

ProgressFn = Callable[..., None]

# how much earlier conversation the agent sees
HISTORY_MESSAGES = 10
# a pending message older than this belongs to a worker that died, don't let it block chat
STALE_AFTER = timedelta(minutes=10)


def next_step_message(node: str, update: dict[str, Any], revising: bool = False) -> str | None:
    # describes what runs next, so the message matches the slow part (the llm call)
    if node == "plan_queries":
        return "Searching company policy."
    if node == "retrieve_policy":
        if revising:
            return "Working on your request."
        found = len(update.get("policy_chunks") or [])
        return f"Drafting the description from {found} policy passages."
    if node == "draft":
        return "Checking the draft against the requirements and policy."
    if node == "review":
        problems = len(update.get("feedback") or [])
        if problems:
            return f"Redrafting to fix {problems} problem{'s' if problems != 1 else ''}."
        return "Putting the description together."
    return None


def build_profile(job: Job) -> dict[str, Any]:
    return {
        "title": job.title,
        "department": job.department,
        "location": job.location,
        "employment_type": job.employment_type,
        "min_years_experience": job.min_years_experience,
        "selection_criteria": job.summary,
        "requirements": [
            {
                "id": r.id,
                "text": r.text,
                "category": r.category.value,
                "is_mandatory": r.is_mandatory,
            }
            for r in job.requirements
        ],
    }


def _run(
    db: Session,
    graph: Any,
    initial: dict[str, Any],
    graph_name: str,
    progress: ProgressFn | None,
) -> dict[str, Any]:
    thread_id = initial["thread_id"]
    revising = bool(initial.get("request"))
    total_steps = 4 + MAX_ATTEMPTS * 2

    def report(message: str, completed: int) -> None:
        if progress:
            progress(state="running", message=message, total=total_steps, completed=completed)

    def invoke() -> dict[str, Any]:
        final: dict[str, Any] = {}
        step = 0
        report("Planning what to look up in company policy.", step)
        for mode, chunk in graph.stream(
            initial, config=thread_config(thread_id), stream_mode=["updates", "values"]
        ):
            if mode == "values":
                final = chunk
                continue
            for node, update in chunk.items():
                step += 1
                message = next_step_message(node, update or {}, revising)
                if message:
                    report(message, step)
        return final

    return run_graph(
        db,
        graph_name=graph_name,
        thread_id=thread_id,
        invoke=invoke,
        context={"job_id": initial["job_id"], "prompt_version": PROMPT_VERSION},
        failure_code=lambda s: None if s.get("description_md") or s.get("reply") else "no_output",
    )


def _failure_message(final: dict[str, Any], fallback: str) -> str:
    messages = [e["message"] for e in final.get("errors", [])]
    return messages[-1] if messages else fallback


def generate_description(
    db: Session,
    job_id: int,
    graph: Any,
    progress: ProgressFn | None = None,
) -> dict[str, Any]:
    job = get_job(db, job_id)
    thread_id = new_thread_id("jd")
    initial = {
        "job_id": job_id,
        "thread_id": thread_id,
        "profile": build_profile(job),
        "errors": [],
    }
    final = _run(db, graph, initial, "job_description", progress)

    if not final.get("description_md"):
        log.info("jd_generation_failed", job_id=job_id)
        raise ExternalServiceError(
            _failure_message(final, "The job description could not be generated.")
        )

    job = get_job(db, job_id)
    citations = final.get("citations", [])
    version = record_version(
        db, job, final["description_md"], citations, DescriptionSource.GENERATED
    )
    db.add(
        JobDescriptionMessage(
            job_id=job_id,
            role=ChatRole.ASSISTANT,
            content=(
                f"I drafted a new description (version {version.version}) using "
                f"{len(citations)} company policy passage{'s' if len(citations) != 1 else ''}. "
                "Tell me what you'd like changed."
            ),
            version_id=version.id,
            warnings=final.get("warnings") or None,
        )
    )
    db.commit()

    log.info(
        "jd_generated",
        job_id=job_id,
        version=version.version,
        attempts=final.get("attempts"),
        citations=len(citations),
        warnings=len(final.get("warnings", [])),
        model=settings.gemini_model,
    )
    return {
        "job_id": job_id,
        "thread_id": thread_id,
        "version": version.version,
        "attempts": final.get("attempts"),
        "citation_count": len(citations),
        "warnings": final.get("warnings", []),
    }


# --- chat ----------------------------------------------------------------------


def list_messages(db: Session, job_id: int) -> list[JobDescriptionMessage]:
    get_job(db, job_id)
    return list(
        db.scalars(
            select(JobDescriptionMessage)
            .where(JobDescriptionMessage.job_id == job_id)
            .order_by(JobDescriptionMessage.id)
        )
    )


def add_user_message(db: Session, job_id: int, text: str, user_id: int) -> JobDescriptionMessage:
    get_job(db, job_id)
    pending = db.scalar(
        select(JobDescriptionMessage).where(
            JobDescriptionMessage.job_id == job_id,
            JobDescriptionMessage.status == ChatMessageStatus.PENDING,
        )
    )
    if pending is not None:
        if _age(pending) < STALE_AFTER:
            raise ConflictError("The agent is still working on your last message.")
        pending.status = ChatMessageStatus.FAILED

    message = JobDescriptionMessage(
        job_id=job_id,
        role=ChatRole.USER,
        content=text.strip(),
        status=ChatMessageStatus.PENDING,
        created_by_id=user_id,
    )
    db.add(message)
    db.commit()
    db.refresh(message)
    log.info("jd_chat_message", job_id=job_id, message_id=message.id)
    return message


def revise_description(
    db: Session,
    job_id: int,
    message_id: int,
    graph: Any,
    progress: ProgressFn | None = None,
) -> dict[str, Any]:
    message = db.get(JobDescriptionMessage, message_id)
    if message is None or message.job_id != job_id:
        raise NotFoundError("That message no longer exists.")
    job = get_job(db, job_id)

    earlier = [
        m
        for m in job.description_messages
        if m.id < message.id and m.status == ChatMessageStatus.DONE
    ][-HISTORY_MESSAGES:]
    prior_chunks = [
        {
            "chunk_id": c["chunk_id"],
            "policy_id": c["policy_id"],
            "policy_title": c["policy_title"],
            "text": c["excerpt"],
            "score": 1.0,
        }
        for c in job.policy_citations or []
    ]
    initial = {
        "job_id": job_id,
        "thread_id": new_thread_id("jdchat"),
        "profile": build_profile(job),
        "request": message.content,
        "current_md": job.description_md,
        "history": [{"role": m.role.value, "content": m.content} for m in earlier],
        "prior_chunks": prior_chunks,
        "errors": [],
    }

    try:
        final = _run(db, graph, initial, "job_description_chat", progress)
        if not final.get("description_md") and not final.get("reply"):
            raise ExternalServiceError(
                _failure_message(final, "The agent could not answer that. Please try again.")
            )
    except AppError as exc:
        _answer_failed(db, job_id, message.id, exc.message)
        raise

    version = None
    new_md = final.get("description_md") or ""
    # the model sometimes says it changed something when the text came out the same
    if final.get("changed") and new_md and new_md.strip() != (job.description_md or "").strip():
        version = record_version(
            db,
            job,
            final["description_md"],
            final.get("citations", []),
            DescriptionSource.CHAT,
            created_by_id=message.created_by_id,
        )
    reply = final.get("reply") or ("Done." if version else "I left the description as it is.")
    db.add(
        JobDescriptionMessage(
            job_id=job_id,
            role=ChatRole.ASSISTANT,
            content=reply,
            version_id=version.id if version else None,
            warnings=final.get("warnings") or None,
        )
    )
    message.status = ChatMessageStatus.DONE
    db.commit()

    log.info(
        "jd_chat_answered",
        job_id=job_id,
        message_id=message.id,
        changed=version is not None,
        attempts=final.get("attempts"),
    )
    return {
        "job_id": job_id,
        "message_id": message.id,
        "changed": version is not None,
        "version": version.version if version else None,
        "warnings": final.get("warnings", []),
    }


def _answer_failed(db: Session, job_id: int, message_id: int, reason: str) -> None:
    db.rollback()
    message = db.get(JobDescriptionMessage, message_id)
    if message is not None:
        message.status = ChatMessageStatus.FAILED
    db.add(
        JobDescriptionMessage(
            job_id=job_id,
            role=ChatRole.ASSISTANT,
            content=f"Sorry, I couldn't do that. {reason}",
            status=ChatMessageStatus.FAILED,
        )
    )
    db.commit()
    log.info("jd_chat_failed", job_id=job_id, message_id=message_id)


def _age(message: JobDescriptionMessage) -> timedelta:
    created = message.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    return datetime.now(UTC) - created
