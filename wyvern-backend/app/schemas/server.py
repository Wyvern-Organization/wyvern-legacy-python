from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import MemberRole


class ServerCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    description: str | None = Field(default=None, max_length=512)
    icon: str | None = None
    directory_opt_in: bool = False


class ServerUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    description: str | None = Field(default=None, max_length=512)
    icon: str | None = None
    directory_opt_in: bool | None = None


class ServerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None
    icon: str | None
    directory_opt_in: bool
    owner_id: int
    created_at: datetime


class ServerMemberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    server_id: int
    user_id: int
    role: MemberRole
    joined_at: datetime


class ServerInviteOut(BaseModel):
    code: str
    server_id: int
    created_by: int
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
