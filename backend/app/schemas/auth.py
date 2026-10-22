from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models.enums import UserRole

# bcrypt limit is 72 bytes
MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 72


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH)
    full_name: str = Field(min_length=1, max_length=200)
    role: UserRole = UserRole.INTERVIEWER


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)


class UserOut(BaseModel):
    # never add password_hash here
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    full_name: str
    role: UserRole
    is_active: bool


class AuthOptions(BaseModel):
    registration_open: bool


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in_minutes: int
    user: UserOut
