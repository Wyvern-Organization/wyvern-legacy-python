from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models.enums import PresenceStatus


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    discriminator: str
    display_name: str | None
    bio: str | None
    directory_opt_in: bool
    email: EmailStr
    avatar: str | None
    is_paid: bool
    created_at: datetime


class PresenceUpdateRequest(BaseModel):
    status: PresenceStatus = Field(description="online, idle, dnd")


class PresenceOut(BaseModel):
    user_id: int
    status: PresenceStatus


class UserDirectoryEntryOut(BaseModel):
    user: UserOut
