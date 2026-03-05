from datetime import datetime

from pydantic import BaseModel

from app.models.enums import ChannelType


class DMCreateRequest(BaseModel):
    recipient_id: int


class DMParticipantOut(BaseModel):
    id: int
    username: str
    discriminator: str
    display_name: str | None
    avatar: str | None


class DMChannelOut(BaseModel):
    id: int
    server_id: int | None
    name: str
    type: ChannelType
    position: int
    category: str | None
    created_by: int
    created_at: datetime
    participants: list[DMParticipantOut]
