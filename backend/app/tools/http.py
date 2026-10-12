# one place that builds http clients for third-party apis, so tests can swap the
# transport for a fake one and never touch the network

from typing import Any

import httpx

from app.errors import ExternalServiceError
from app.logging_config import get_logger

log = get_logger(__name__)

TIMEOUT = httpx.Timeout(30.0, connect=10.0)

_transport: httpx.BaseTransport | None = None


def set_transport(transport: httpx.BaseTransport | None) -> None:
    global _transport
    _transport = transport


def client() -> httpx.Client:
    return httpx.Client(timeout=TIMEOUT, transport=_transport, follow_redirects=True)


class ProviderError(ExternalServiceError):
    # carries the provider status so callers can tell "reconnect" from "try again"
    def __init__(self, message: str, status: int | None = None, provider: str = "") -> None:
        super().__init__(message, {"provider": provider} if provider else None)
        self.status = status


def check(response: httpx.Response, provider: str, action: str) -> httpx.Response:
    if response.is_success:
        return response
    status = response.status_code
    # the body can echo tokens or applicant data, so only the status is logged
    log.warning("provider_call_failed", provider=provider, action=action, status=status)
    name = {"linkedin": "LinkedIn", "google": "Google"}.get(provider, provider)
    if status in (401, 403):
        message = f"{name} refused the request. Reconnect {name} in Settings → Integrations."
    elif status == 404:
        message = f"{name} could not find what we asked for ({action})."
    elif status == 429:
        message = f"{name} is rate limiting us. Please try again in a few minutes."
    else:
        message = f"{name} could not complete the request ({action}). Please try again."
    raise ProviderError(message, status=status, provider=provider)


def json_or_empty(response: httpx.Response) -> dict[str, Any]:
    try:
        return response.json()
    except ValueError:
        return {}
