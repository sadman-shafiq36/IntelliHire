# log ids, not people: cv_id=42 is fine, a candidate name is not

import logging
import re
import sys
from typing import Any

import structlog

from app.config import settings

# values under these keys are always blanked
SENSITIVE_KEYS = frozenset(
    {
        "password",
        "passwd",
        "secret",
        "token",
        "access_token",
        "refresh_token",
        "authorization",
        "api_key",
        "google_api_key",
        "jwt_secret_key",
        "smtp_password",
        "postgres_password",
        "candidate_name",
        "full_name",
        "email",
        "phone",
        "address",
        "cv_text",
        "transcript_text",
        "prompt",
    }
)

REDACTED = "[redacted]"

# catches emails hidden inside message strings
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def _mask_emails(text: str) -> str:
    return _EMAIL_RE.sub(REDACTED, text)


def redact_processor(_logger: Any, _method_name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    for key, value in list(event_dict.items()):
        if key.lower() in SENSITIVE_KEYS:
            event_dict[key] = REDACTED
        elif isinstance(value, str):
            event_dict[key] = _mask_emails(value)
    return event_dict


def configure_logging() -> None:
    level = getattr(logging, settings.log_level, logging.INFO)

    # send uvicorn / sqlalchemy logs through the same handler
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level)

    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        redact_processor,
    ]

    # json in production, readable key=value locally
    renderer: Any = (
        structlog.processors.JSONRenderer()
        if settings.is_production
        else structlog.dev.ConsoleRenderer(colors=False)
    )

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> Any:
    return structlog.get_logger(name)
