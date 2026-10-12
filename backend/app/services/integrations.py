# connected linkedin and google accounts. tokens are encrypted with a key derived
# from JWT_SECRET_KEY; rotating that secret means reconnecting, which is fine

import base64
import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.errors import InvalidInputError, NotFoundError
from app.logging_config import get_logger
from app.models.enums import IntegrationProvider
from app.models.integration import IntegrationAccount
from app.tools import google_forms, linkedin
from app.tools.http import ProviderError

log = get_logger(__name__)

STATE_TTL = timedelta(minutes=10)
# refresh google a little before it actually expires
REFRESH_MARGIN = timedelta(minutes=2)

LABEL = {IntegrationProvider.LINKEDIN: "LinkedIn", IntegrationProvider.GOOGLE: "Google"}


def _fernet() -> Fernet:
    digest = hashlib.sha256(
        b"intellihire-integrations:" + settings.jwt_secret_key.encode()
    ).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    return _fernet().decrypt(value.encode()).decode()


def parse_provider(raw: str) -> IntegrationProvider:
    try:
        return IntegrationProvider(raw)
    except ValueError as exc:
        raise NotFoundError("That integration does not exist.") from exc


def _client_for(provider: IntegrationProvider):
    return linkedin if provider == IntegrationProvider.LINKEDIN else google_forms


# --- oauth state ------------------------------------------------------------------
# the callback arrives as a plain browser redirect without our bearer token, so the
# state carries who started the connection, signed and short-lived (also stops csrf)


def make_state(provider: IntegrationProvider, user_id: int) -> str:
    now = datetime.now(UTC)
    payload = {
        "purpose": "oauth",
        "provider": provider.value,
        "sub": str(user_id),
        "nonce": secrets.token_urlsafe(8),
        "iat": now,
        "exp": now + STATE_TTL,
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def read_state(state: str, provider: IntegrationProvider) -> int:
    try:
        payload = jwt.decode(state, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    except jwt.InvalidTokenError as exc:
        raise InvalidInputError("The connection request expired. Please try again.") from exc
    if payload.get("purpose") != "oauth" or payload.get("provider") != provider.value:
        raise InvalidInputError("The connection request was not valid. Please try again.")
    return int(payload["sub"])


# --- connect / disconnect ------------------------------------------------------------

CANCELLED = {"access_denied", "user_cancelled_login", "user_cancelled_authorize"}


def provider_error_message(
    provider: IntegrationProvider, error: str | None, description: str | None
) -> str:
    if not error or error in CANCELLED:
        return "declined"
    detail = (description or error).replace("+", " ").strip()[:300]
    return f"{LABEL[provider]} refused the connection: {detail}"


# --- connect / disconnect ------------------------------------------------------------


def connect_url(provider: IntegrationProvider, user_id: int) -> str:
    tool = _client_for(provider)
    if not tool.is_configured():
        raise InvalidInputError(
            f"{LABEL[provider]} is not set up yet. Add its client ID and secret to .env "
            "(see PROJECT/integrations-setup.md) and restart the backend."
        )
    return tool.authorize_url(make_state(provider, user_id))


def complete_connection(db: Session, provider: IntegrationProvider, code: str, state: str) -> None:
    user_id = read_state(state, provider)
    if provider == IntegrationProvider.LINKEDIN:
        tokens = linkedin.exchange_code(code)
        member = linkedin.current_member(tokens.access_token)
        account_id, account_name = member.id, member.name
    else:
        tokens = google_forms.exchange_code(code)
        email, name = google_forms.current_user(tokens.access_token)
        # google only returns a name with the profile scope, which we don't ask for
        account_id, account_name = email, name or email

    account = _get(db, provider) or IntegrationAccount(provider=provider)
    previous_refresh = account.refresh_token_enc
    account.account_id = account_id
    account.account_name = account_name
    account.access_token_enc = encrypt(tokens.access_token)
    # google only sends a refresh token on the first consent, keep the old one otherwise
    account.refresh_token_enc = (
        encrypt(tokens.refresh_token) if tokens.refresh_token else previous_refresh
    )
    account.expires_at = _expiry(tokens.expires_in)
    account.scopes = tokens.scope
    account.connected_by_id = user_id
    db.add(account)
    db.commit()
    log.info("integration_connected", provider=provider.value, user_id=user_id)


def disconnect(db: Session, provider: IntegrationProvider) -> None:
    account = _get(db, provider)
    if account is not None:
        db.delete(account)
        db.commit()
        log.info("integration_disconnected", provider=provider.value)


def status(db: Session) -> list[dict[str, Any]]:
    out = []
    for provider in IntegrationProvider:
        account = _get(db, provider)
        configured = _client_for(provider).is_configured()
        expired = bool(
            account
            and account.expires_at
            and _aware(account.expires_at) <= datetime.now(UTC)
            and not account.refresh_token_enc
        )
        out.append(
            {
                "provider": provider.value,
                "label": LABEL[provider],
                "configured": configured,
                "connected": account is not None and not expired,
                "expired": expired,
                "account_name": account.account_name if account else None,
                "expires_at": account.expires_at if account else None,
                "template_configured": (
                    bool(settings.google_form_template_id)
                    if provider == IntegrationProvider.GOOGLE
                    else None
                ),
            }
        )
    return out


# --- tokens for callers ----------------------------------------------------------------


def access_token(db: Session, provider: IntegrationProvider) -> tuple[str, IntegrationAccount]:
    account = _get(db, provider)
    label = LABEL[provider]
    if account is None:
        raise ProviderError(
            f"{label} is not connected. Connect it in Settings → Integrations.",
            provider=provider.value,
        )
    try:
        token = decrypt(account.access_token_enc)
    except InvalidToken as exc:
        raise ProviderError(
            f"The saved {label} connection can't be read any more. Please reconnect {label}.",
            provider=provider.value,
        ) from exc

    if account.expires_at is None or _aware(account.expires_at) - REFRESH_MARGIN > datetime.now(
        UTC
    ):
        return token, account

    if provider == IntegrationProvider.GOOGLE and account.refresh_token_enc:
        tokens = google_forms.refresh(decrypt(account.refresh_token_enc))
        account.access_token_enc = encrypt(tokens.access_token)
        account.expires_at = _expiry(tokens.expires_in)
        db.commit()
        log.info("integration_refreshed", provider=provider.value)
        return tokens.access_token, account

    raise ProviderError(
        f"The {label} connection has expired. Reconnect {label} in Settings → Integrations.",
        provider=provider.value,
    )


def _get(db: Session, provider: IntegrationProvider) -> IntegrationAccount | None:
    return db.scalar(select(IntegrationAccount).where(IntegrationAccount.provider == provider))


def _expiry(expires_in: int | None) -> datetime | None:
    return datetime.now(UTC) + timedelta(seconds=expires_in) if expires_in else None


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)
