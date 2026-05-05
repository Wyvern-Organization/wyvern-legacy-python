from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.utils.validation import MAX_ATTACHMENTS, validate_public_url, validate_public_url_list


class WebhookCreateRequest(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    description: str | None = Field(default=None, max_length=255)
    channel_id: str


class WebhookMessageRequest(BaseModel):
    content: str = Field(default="", max_length=4000)
    attachments: list[str] = Field(default_factory=list, max_length=MAX_ATTACHMENTS)
    username: str | None = Field(default=None, max_length=120)
    avatar_url: str | None = Field(default=None, max_length=1024)
    reply_to_id: str | None = None

    @field_validator("attachments")
    @classmethod
    def validate_attachments(cls, value: list[str]) -> list[str]:
        return validate_public_url_list(value)

    @field_validator("avatar_url")
    @classmethod
    def validate_avatar_url(cls, value: str | None) -> str | None:
        return validate_public_url(value, field_name="avatar_url")


class WebhookOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    server_id: str
    channel_id: str
    name: str
    description: str | None
    active: bool
    created_by: str | None
    created_at: datetime
    updated_at: datetime
    last_used_at: datetime | None = None
    webhook_url: str | None = None


class WebhookCreateOut(BaseModel):
    webhook: WebhookOut
    token: str
    webhook_url: str


class WebhookDeliveryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    webhook_id: str
    request_id: str
    status: str
    attempts: int
    response_message: str | None = None
    created_at: datetime


class WorkspaceRevisionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    editor_user_id: str | None = None
    content: str
    created_at: datetime


class WorkspaceDocumentOut(BaseModel):
    id: str | None = None
    channel_id: str
    title: str
    mode: str
    language: str
    visibility: str
    content: str
    owner_user_id: str | None = None
    updated_by_user_id: str | None = None
    created_at: datetime
    updated_at: datetime
    revisions: list[WorkspaceRevisionOut] = Field(default_factory=list)


class WorkspaceUpdateRequest(BaseModel):
    title: str | None = Field(default=None, max_length=120)
    mode: str | None = Field(default=None, max_length=16)
    language: str | None = Field(default=None, max_length=32)
    visibility: str | None = Field(default=None, max_length=16)
    content: str = Field(default="", max_length=200_000)
    log_activity: bool = False


class CommunityActivityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    server_id: str | None = None
    actor_user_id: str | None = None
    action: str
    target_type: str | None = None
    target_id: str | None = None
    activity_metadata: dict | None = None
    created_at: datetime
