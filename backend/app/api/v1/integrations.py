from datetime import datetime
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import settings
from app.db.session import get_db
from app.deps import require_interviewer
from app.errors import AppError
from app.logging_config import get_logger
from app.models.user import User
from app.services import integrations as service

router = APIRouter(prefix="/integrations", tags=["integrations"])
log = get_logger(__name__)


class IntegrationStatus(BaseModel):
    provider: str
    label: str
    configured: bool
    connected: bool
    expired: bool
    account_name: str | None
    expires_at: datetime | None
    template_configured: bool | None
    scheduling_ready: bool | None = None


class ConnectResponse(BaseModel):
    authorize_url: str


@router.get("", response_model=list[IntegrationStatus], summary="LinkedIn and Google status")
def list_integrations(
    db: Session = Depends(get_db),
    _user: User = Depends(require_interviewer),
) -> list[IntegrationStatus]:
    return [IntegrationStatus(**item) for item in service.status(db)]


@router.post(
    "/{provider}/connect",
    response_model=ConnectResponse,
    summary="Start connecting an account; the browser goes to the returned URL",
)
def connect(provider: str, user: User = Depends(require_interviewer)) -> ConnectResponse:
    return ConnectResponse(
        authorize_url=service.connect_url(service.parse_provider(provider), user.id)
    )


@router.get(
    "/{provider}/callback",
    include_in_schema=False,
    summary="Where LinkedIn or Google send the browser back after consent",
)
def callback(
    provider: str,
    code: str | None = Query(None),
    state: str | None = Query(None),
    error: str | None = Query(None),
    error_description: str | None = Query(None),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    # no bearer token on a redirect, the signed state identifies the interviewer
    target = f"{settings.public_base_url.rstrip('/')}/settings/integrations"
    parsed = service.parse_provider(provider)
    if error or not code or not state:
        # provider error codes are safe to log, the description is shown to the user
        log.info("integration_consent_failed", provider=parsed.value, error_code=error)
        message = service.provider_error_message(parsed, error, error_description)
        return RedirectResponse(f"{target}?{urlencode({'error': message, 'provider': provider})}")
    try:
        service.complete_connection(db, parsed, code, state)
    except AppError as exc:
        return RedirectResponse(
            f"{target}?{urlencode({'error': exc.message, 'provider': provider})}"
        )
    return RedirectResponse(f"{target}?{urlencode({'connected': provider})}")


@router.delete(
    "/{provider}", status_code=status.HTTP_204_NO_CONTENT, summary="Disconnect an account"
)
def disconnect(
    provider: str,
    db: Session = Depends(get_db),
    _user: User = Depends(require_interviewer),
) -> None:
    service.disconnect(db, service.parse_provider(provider))
