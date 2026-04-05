from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class MessageCreate(BaseModel):
    content: str = Field(default="", max_length=4000)
    attachments: list[str] = Field(default_factory=list)
    reply_to_id: int | None = None


class MessageUpdate(BaseModel):
    content: str = Field(min_length=0, max_length=4000)


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    channel_id: int
    author_id: int
    reply_to_id: int | None = None
    content: str
    attachments: list[str]
    created_at: datetime
    edited_at: datetime | None
    reply_to: "MessageReplyPreviewOut | None" = None
    reactions: list["MessageReactionOut"] = Field(default_factory=list)


class MessageReplyPreviewOut(BaseModel):
    id: int
    author_id: int
    content: str
    attachments: list[str]
    created_at: datetime
    edited_at: datetime | None = None


class MessageReactionOut(BaseModel):
    emoji: str
    count: int
    users: list[int] = Field(default_factory=list)


class MessageHistoryOut(BaseModel):
    items: list[MessageOut]
    next_cursor: int | None


class ReactionPayload(BaseModel):
    emoji: str = Field(min_length=1, max_length=64)


MessageOut.model_rebuild()
