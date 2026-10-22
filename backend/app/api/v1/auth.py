# sign-out is client side (drop the token). tokens stay valid until they
# expire, so ACCESS_TOKEN_EXPIRE_MINUTES is the real logout control

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db.session import get_db
from app.deps import get_current_user
from app.errors import AuthenticationError, ConflictError, PermissionDeniedError
from app.logging_config import get_logger
from app.models.user import User
from app.schemas.auth import AuthOptions, LoginRequest, RegisterRequest, TokenResponse, UserOut
from app.services.security import create_access_token, hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["authentication"])
log = get_logger(__name__)


def _issue_token(user: User) -> TokenResponse:
    token = create_access_token(subject=str(user.id), role=user.role.value)
    return TokenResponse(
        access_token=token,
        expires_in_minutes=settings.access_token_expire_minutes,
        user=UserOut.model_validate(user),
    )


@router.post(
    "/register",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an account and sign in",
)
def register(payload: RegisterRequest, db: Session = Depends(get_db)) -> TokenResponse:
    # off when the app is on a public address, or anyone could sign up as an interviewer
    if not settings.allow_registration:
        raise PermissionDeniedError(
            "New accounts can't be created here. Ask the hiring team to set one up for you."
        )
    normalised_email = payload.email.lower().strip()

    existing = db.scalar(select(User).where(User.email == normalised_email))
    if existing is not None:
        raise ConflictError("An account with that email address already exists.")

    user = User(
        email=normalised_email,
        password_hash=hash_password(payload.password),
        full_name=payload.full_name.strip(),
        role=payload.role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    log.info("user_registered", user_id=user.id, role=user.role.value)
    return _issue_token(user)


@router.get("/options", response_model=AuthOptions, summary="Whether new accounts can be created")
def read_options() -> AuthOptions:
    return AuthOptions(registration_open=settings.allow_registration)


@router.post("/login", response_model=TokenResponse, summary="Sign in")
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> TokenResponse:
    user = db.scalar(select(User).where(User.email == payload.email.lower().strip()))

    # same message for unknown account and wrong password, no enumeration
    if user is None or not verify_password(payload.password, user.password_hash):
        log.info("login_failed")
        raise AuthenticationError("Incorrect email address or password.")

    if not user.is_active:
        raise AuthenticationError("This account has been deactivated.")

    log.info("login_succeeded", user_id=user.id, role=user.role.value)
    return _issue_token(user)


@router.get("/me", response_model=UserOut, summary="The signed-in user")
def read_current_user(user: User = Depends(get_current_user)) -> UserOut:
    return UserOut.model_validate(user)
