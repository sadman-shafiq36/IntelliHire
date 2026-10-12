# protected endpoints put the guard in their signature:
#   def read_score(user: User = Depends(require_interviewer)): ...

from collections.abc import Callable

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.errors import AuthenticationError, PermissionDeniedError
from app.models.enums import UserRole
from app.models.user import User
from app.services.security import decode_access_token


def _extract_bearer_token(request: Request) -> str:
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise AuthenticationError("Sign in to continue.")
    return token


def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
) -> User:
    token = _extract_bearer_token(request)
    claims = decode_access_token(token)

    user_id = claims.get("sub")
    if user_id is None:
        raise AuthenticationError("Could not validate your credentials.")

    user = db.get(User, int(user_id))
    if user is None or not user.is_active:
        # same message as a bad token so callers can't probe for accounts
        raise AuthenticationError("Could not validate your credentials.")
    return user


def require_role(*allowed: UserRole) -> Callable[..., User]:
    def dependency(user: User = Depends(get_current_user)) -> User:
        if user.role not in allowed:
            raise PermissionDeniedError("Your account does not have access to this resource.")
        return user

    return dependency


require_interviewer = require_role(UserRole.INTERVIEWER)
require_candidate = require_role(UserRole.CANDIDATE)
