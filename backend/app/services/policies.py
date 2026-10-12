import uuid
from collections.abc import Callable
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.errors import ExternalServiceError, InvalidInputError, NotFoundError
from app.logging_config import get_logger
from app.models.enums import PolicyStatus
from app.models.job import CompanyPolicy
from app.rag.policy_store import get_policy_store
from app.services import ai
from app.services.documents import DocumentReadError, chunk_text, extract_text

log = get_logger(__name__)

ProgressFn = Callable[..., None]


def _policy_dir() -> Path:
    path = Path(settings.storage_dir) / "policies"
    path.mkdir(parents=True, exist_ok=True)
    return path


def save_policy_upload(
    db: Session,
    *,
    title: str,
    filename: str,
    content_type: str,
    data: bytes,
    uploaded_by_id: int | None,
) -> CompanyPolicy:
    suffix = Path(filename).suffix.lower()
    if suffix not in settings.allowed_policy_extension_list:
        allowed = ", ".join(settings.allowed_policy_extension_list)
        raise InvalidInputError(f"Please upload one of these file types: {allowed}.")
    if not data:
        raise InvalidInputError("The file is empty.")
    if len(data) > settings.max_upload_bytes:
        raise InvalidInputError(f"The file is larger than {settings.max_upload_mb} MB.")

    # stored under a random name, the original name is only kept for display
    stored = _policy_dir() / f"{uuid.uuid4().hex}{suffix}"
    stored.write_bytes(data)

    policy = CompanyPolicy(
        title=title.strip() or Path(filename).stem,
        original_filename=Path(filename).name[:300],
        storage_path=str(stored),
        content_type=content_type[:120] or "application/octet-stream",
        size_bytes=len(data),
        status=PolicyStatus.UPLOADED,
        uploaded_by_id=uploaded_by_id,
    )
    db.add(policy)
    db.commit()
    db.refresh(policy)
    log.info("policy_uploaded", policy_id=policy.id, size_bytes=policy.size_bytes)
    return policy


def index_policy(db: Session, policy_id: int, progress: ProgressFn | None = None) -> CompanyPolicy:
    def report(**kwargs) -> None:
        if progress:
            progress(**kwargs)

    policy = db.get(CompanyPolicy, policy_id)
    if policy is None:
        raise NotFoundError("That policy no longer exists.")

    policy.status = PolicyStatus.INDEXING
    policy.failure_reason = None
    db.commit()

    try:
        report(state="running", message="Reading the document.", total=3, completed=0)
        text = extract_text(Path(policy.storage_path))

        report(state="running", message="Splitting into passages.", total=3, completed=1)
        chunks = chunk_text(text, settings.policy_chunk_chars, settings.policy_chunk_overlap)

        report(state="running", message=f"Indexing {len(chunks)} passages.", total=3, completed=2)
        embeddings = ai.embed_texts(chunks)
        get_policy_store().add_policy(policy.id, policy.title, chunks, embeddings)
    except DocumentReadError as exc:
        _mark_failed(db, policy, str(exc))
        return policy
    except ExternalServiceError as exc:
        _mark_failed(db, policy, exc.message)
        raise
    except Exception:
        _mark_failed(db, policy, "Indexing failed unexpectedly. Please try again.")
        raise

    policy.status = PolicyStatus.INDEXED
    policy.chunk_count = len(chunks)
    db.commit()
    log.info("policy_indexed", policy_id=policy.id, chunk_count=len(chunks))
    return policy


def _mark_failed(db: Session, policy: CompanyPolicy, reason: str) -> None:
    policy.status = PolicyStatus.FAILED
    policy.chunk_count = 0
    policy.failure_reason = reason[:500]
    db.commit()
    log.info("policy_index_failed", policy_id=policy.id)


def get_policy(db: Session, policy_id: int) -> CompanyPolicy:
    policy = db.get(CompanyPolicy, policy_id)
    if policy is None:
        raise NotFoundError("That policy no longer exists.")
    return policy


def list_policies(db: Session) -> list[CompanyPolicy]:
    return list(db.scalars(select(CompanyPolicy).order_by(CompanyPolicy.created_at.desc())))


def delete_policy(db: Session, policy_id: int) -> None:
    policy = get_policy(db, policy_id)
    get_policy_store().delete_policy(policy.id)
    Path(policy.storage_path).unlink(missing_ok=True)
    db.delete(policy)
    db.commit()
    log.info("policy_deleted", policy_id=policy_id)
