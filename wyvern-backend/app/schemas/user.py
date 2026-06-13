from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models.enums import PresenceStatus


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    username: str
    discriminator: str
    display_name: str | None
    bio: str | None
    directory_opt_in: bool
    email: EmailStr
    avatar: str | None
    is_paid: bool
    created_at: datetime


class UserMeOut(UserOut):
    is_admin: bool = False
    accepted_terms_version: str | None = None
    accepted_privacy_version: str | None = None
    legal_accepted_at: datetime | None = None
    legal_reaccept_required: bool = False
    ai_opt_in: bool = False
    nsfw_18_verified: bool = False


class UserPublicOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    username: str
    discriminator: str
    display_name: str | None
    bio: str | None
    directory_opt_in: bool
    avatar: str | None
    created_at: datetime


class PresenceUpdateRequest(BaseModel):
    status: PresenceStatus = Field(description="online, idle, dnd")


class PresenceOut(BaseModel):
    user_id: str
    status: PresenceStatus


class UserDirectoryEntryOut(BaseModel):
    user: UserOut
