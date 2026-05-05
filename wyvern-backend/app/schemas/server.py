from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import MemberRole
from app.utils.validation import validate_public_url


class ServerCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    description: str | None = Field(default=None, max_length=512)
    icon: str | None = None
    directory_opt_in: bool = False

    @field_validator("icon")
    @classmethod
    def validate_icon(cls, value: str | None) -> str | None:
        return validate_public_url(value, field_name="icon")


class ServerUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    description: str | None = Field(default=None, max_length=512)
    icon: str | None = None
    directory_opt_in: bool | None = None

    @field_validator("icon")
    @classmethod
    def validate_icon(cls, value: str | None) -> str | None:
        return validate_public_url(value, field_name="icon")


class ServerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: str | None
    icon: str | None
    directory_opt_in: bool
    owner_id: str
    created_at: datetime


class ServerMemberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    server_id: str
    user_id: str
    role: MemberRole
    joined_at: datetime


class ServerInviteOut(BaseModel):
    code: str
    server_id: str
    created_by: str
    created_at: datetime
    invite_path: str


class ServerInviteLookupOut(BaseModel):
    code: str
    server: ServerOut


class ServerJoinByInviteOut(BaseModel):
    server: ServerOut
    membership: ServerMemberOut


class ServerDirectoryOut(BaseModel):
    server: ServerOut
    member_count: int
    joined: bool
