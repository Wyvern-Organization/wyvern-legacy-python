from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.utils.validation import validate_username_handle


class RegisterRequest(BaseModel):
    username: str = Field(min_length=2, max_length=32)
    display_name: str | None = Field(default=None, min_length=1, max_length=64)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    accepted_legal: bool
    terms_version: str = Field(min_length=1, max_length=32)
    privacy_version: str = Field(min_length=1, max_length=32)

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


class LegalAcceptanceRequest(BaseModel):
    accepted_legal: bool
    terms_version: str = Field(min_length=1, max_length=32)
    privacy_version: str = Field(min_length=1, max_length=32)


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
    accepted_terms_version: str | None = None
    accepted_privacy_version: str | None = None
    legal_accepted_at: datetime | None = None
    legal_reaccept_required: bool = False
    ai_opt_in: bool = False
    nsfw_18_verified: bool = False


class EdgeHandoffOut(BaseModel):
    grant: str
    expires_at: datetime
