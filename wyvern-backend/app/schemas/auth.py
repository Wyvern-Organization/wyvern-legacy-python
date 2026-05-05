from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.utils.validation import validate_username_handle


class RegisterRequest(BaseModel):
    username: str = Field(min_length=2, max_length=32)
    display_name: str | None = Field(default=None, min_length=1, max_length=64)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)

    @field_validator("username")
    @classmethod
    def validate_username(cls, value: str) -> str:
        return validate_username_handle(value)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: str


class EdgeExchangeRequest(BaseModel):
    grant: str


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class AuthUser(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    username: str
    discriminator: str
    display_name: str | None
    bio: str | None
    directory_opt_in: bool
    email: EmailStr
    avatar: str | None


class EdgeHandoffOut(BaseModel):
    grant: str
    expires_at: datetime
