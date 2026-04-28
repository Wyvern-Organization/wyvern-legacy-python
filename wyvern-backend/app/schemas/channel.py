from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import ChannelType


class ChannelCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    type: ChannelType = ChannelType.text
    position: int = 0
    category: str | None = None


class ChannelUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    position: int | None = None
    category: str | None = None


class ChannelOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    server_id: str | None
    name: str
    type: ChannelType
    position: int
    category: str | None
    created_by: str
    created_at: datetime
