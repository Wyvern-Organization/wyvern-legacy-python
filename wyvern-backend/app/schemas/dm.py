from datetime import datetime

from pydantic import BaseModel

from app.models.enums import ChannelType


class DMCreateRequest(BaseModel):
    recipient_id: str


class DMParticipantOut(BaseModel):
    id: str
    username: str
    discriminator: str
    display_name: str | None
    avatar: str | None


class DMChannelOut(BaseModel):
    id: str
    server_id: str | None
    name: str
    type: ChannelType
    position: int
    category: str | None
    created_by: str
    created_at: datetime
    participants: list[DMParticipantOut]
