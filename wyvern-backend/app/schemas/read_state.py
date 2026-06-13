from datetime import datetime

from pydantic import BaseModel, Field


class ChannelReadStateUpdateRequest(BaseModel):
    last_read_message_id: str | None = Field(default=None, min_length=1)


class ChannelReadStateOut(BaseModel):
    channel_id: str
    last_read_message_id: str | None
    last_read_at: datetime
    updated_at: datetime
