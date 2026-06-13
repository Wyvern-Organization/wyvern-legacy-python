from datetime import datetime
from datetime import date

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.utils.validation import MAX_ATTACHMENTS, validate_public_url_list


class MessageCreate(BaseModel):
    content: str = Field(default="", max_length=4000)
    attachments: list[str] = Field(default_factory=list, max_length=MAX_ATTACHMENTS)
    reply_to_id: str | None = None
    is_nsfw: bool = False

    @field_validator("attachments")
    @classmethod
    def validate_attachments(cls, value: list[str]) -> list[str]:
        return validate_public_url_list(value)


class MessageUpdate(BaseModel):
    content: str = Field(min_length=0, max_length=4000)


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    channel_id: str
    author_id: str
    reply_to_id: str | None = None
    content: str
    attachments: list[str]
    created_at: datetime
    edited_at: datetime | None
    is_pinned: bool = False
    is_nsfw: bool = False
    webhook_name: str | None = None
    webhook_avatar: str | None = None
    bookmarked_by_me: bool = False
    reply_to: "MessageReplyPreviewOut | None" = None
    reactions: list["MessageReactionOut"] = Field(default_factory=list)


class MessageReplyPreviewOut(BaseModel):
    id: str
    author_id: str
    content: str
    attachments: list[str]
    created_at: datetime
    edited_at: datetime | None = None
    is_nsfw: bool = False


class MessageReactionOut(BaseModel):
    emoji: str
    count: int
    users: list[str] = Field(default_factory=list)


class MessageHistoryOut(BaseModel):
    items: list[MessageOut]
    next_cursor: str | None


class ReactionPayload(BaseModel):
    emoji: str = Field(min_length=1, max_length=64)


class SearchRequest(BaseModel):
    q: str = Field(default="", max_length=200)
    channel_id: str | None = None
    server_id: str | None = None
    author_id: str | None = None
    before: str | None = None
    after: str | None = None
    has_attachments: bool | None = None
    has_reactions: bool | None = None
    is_pinned: bool | None = None
    created_before: date | None = None
    created_after: date | None = None
    limit: int = Field(default=25, ge=1, le=100)


class MessageSearchHitOut(BaseModel):
    message: MessageOut
    channel_id: str
    channel_name: str
    channel_type: str
    server_id: str | None = None
    server_name: str | None = None


class MessageBookmarkOut(BaseModel):
    saved_at: datetime
    message: MessageOut


class MessagePinListOut(BaseModel):
    items: list[MessageOut] = Field(default_factory=list)


class BookmarkListOut(BaseModel):
    items: list[MessageBookmarkOut] = Field(default_factory=list)


MessageOut.model_rebuild()
