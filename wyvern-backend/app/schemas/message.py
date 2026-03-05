from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class MessageCreate(BaseModel):
    content: str = Field(default="", max_length=4000)
    attachments: list[str] = Field(default_factory=list)


class MessageUpdate(BaseModel):
    content: str = Field(min_length=0, max_length=4000)


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    channel_id: int
    author_id: int
    content: str
    attachments: list[str]
    created_at: datetime
    edited_at: datetime | None


class MessageHistoryOut(BaseModel):
    items: list[MessageOut]
    next_cursor: int | None


class ReactionPayload(BaseModel):
    emoji: str = Field(min_length=1, max_length=64)
