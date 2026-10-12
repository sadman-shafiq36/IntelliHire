# local auth instead of firebase, see PROJECT/decisions.md

import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import bcrypt
import jwt

from app.config import settings
from app.errors import AuthenticationError

# bcrypt silently truncates past 72 bytes, so reject longer input
MAX_PASSWORD_BYTES = 72


def hash_password(plain_password: str) -> str:
    # salt is embedded in the output
    encoded = plain_password.encode("utf-8")
    if len(encoded) > MAX_PASSWORD_BYTES:
        raise ValueError("Password must be at most 72 bytes.")
    return bcrypt.hashpw(encoded, bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, password_hash: str) -> bool:
    # a malformed stored hash returns False instead of a 500
    try:
        return bcrypt.checkpw(plain_password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def create_access_token(subject: str, role: str, expires_minutes: int | None = None) -> str:
    # only id and role in the token, a jwt is readable by anyone holding it
    if not settings.jwt_secret_key:
        # an empty key would let anyone mint tokens
        raise RuntimeError("JWT_SECRET_KEY is not configured.")

    minutes = expires_minutes or settings.access_token_expire_minutes
    now = datetime.now(UTC)
    payload = {
        "sub": str(subject),
        "role": role,
        "iat": now,
        "exp": now + timedelta(minutes=minutes),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
    except jwt.ExpiredSignatureError as exc:
        raise AuthenticationError("Your session has expired. Please sign in again.") from exc
    except jwt.InvalidTokenError as exc:
        raise AuthenticationError("Could not validate your credentials.") from exc


def generate_join_token() -> str:
    # 256 bits, the link is the candidate's only credential
    return secrets.token_urlsafe(32)
