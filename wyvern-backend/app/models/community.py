from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.json_types import json_value_type
from app.models.sync import SyncMixin
from app.services.ids import ID_COLUMN_LENGTH, generate_entity_id


class MessageBookmark(SyncMixin, Base):
    __tablename__ = "message_bookmarks"
    __table_args__ = (UniqueConstraint("user_id", "message_id", name="uq_message_bookmarks_user_message"),)

    id: Mapped[str] = mapped_column(String(ID_COLUMN_LENGTH), primary_key=True, index=True, default=lambda: generate_entity_id("message_bookmark"))
    legacy_id: Mapped[int | None] = mapped_column(Integer, nullable=True, unique=True, index=True)
    user_id: Mapped[str] = mapped_column(String(ID_COLUMN_LENGTH), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    legacy_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    message_id: Mapped[str] = mapped_column(String(ID_COLUMN_LENGTH), ForeignKey("messages.id", ondelete="CASCADE"), nullable=False, index=True)
    legacy_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    user = relationship("User")
    message = relationship("Message")


class ServerWebhook(SyncMixin, Base):
    __tablename__ = "server_webhooks"

    id: Mapped[str] = mapped_column(String(ID_COLUMN_LENGTH), primary_key=True, index=True, default=lambda: generate_entity_id("server_webhook"))
    legacy_id: Mapped[int | None] = mapped_column(Integer, nullable=True, unique=True, index=True)
    server_id: Mapped[str] = mapped_column(String(ID_COLUMN_LENGTH), ForeignKey("servers.id", ondelete="CASCADE"), nullable=False, index=True)
    legacy_server_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    channel_id: Mapped[str] = mapped_column(String(ID_COLUMN_LENGTH), ForeignKey("channels.id", ondelete="CASCADE"), nullable=False, index=True)
    legacy_channel_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    created_by: Mapped[str | None] = mapped_column(String(ID_COLUMN_LENGTH), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    legacy_created_by: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    server = relationship("Server")
    channel = relationship("Channel")
    creator = relationship("User")
    deliveries = relationship("WebhookDeliveryLog", back_populates="webhook", cascade="all,delete-orphan")


class WebhookDeliveryLog(Base):
    __tablename__ = "webhook_delivery_logs"

    id: Mapped[str] = mapped_column(String(ID_COLUMN_LENGTH), primary_key=True, index=True, default=lambda: generate_entity_id("webhook_delivery_log"))
    legacy_id: Mapped[int | None] = mapped_column(Integer, nullable=True, unique=True, index=True)
    webhook_id: Mapped[str] = mapped_column(String(ID_COLUMN_LENGTH), ForeignKey("server_webhooks.id", ondelete="CASCADE"), nullable=False, index=True)
    legacy_webhook_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    request_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    payload: Mapped[dict | None] = mapped_column(json_value_type, nullable=True)
    response_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    webhook = relationship("ServerWebhook", back_populates="deliveries")


class WorkspaceDocument(SyncMixin, Base):
    __tablename__ = "workspace_documents"

    id: Mapped[str] = mapped_column(String(ID_COLUMN_LENGTH), primary_key=True, index=True, default=lambda: generate_entity_id("workspace_document"))
    legacy_id: Mapped[int | None] = mapped_column(Integer, nullable=True, unique=True, index=True)
    channel_id: Mapped[str] = mapped_column(String(ID_COLUMN_LENGTH), ForeignKey("channels.id", ondelete="CASCADE"), nullable=False, index=True)
    legacy_channel_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False, default="writing", server_default="writing")
    language: Mapped[str] = mapped_column(String(32), nullable=False, default="plaintext", server_default="plaintext")
    visibility: Mapped[str] = mapped_column(String(16), nullable=False, default="public", server_default="public")
    content: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    owner_user_id: Mapped[str | None] = mapped_column(String(ID_COLUMN_LENGTH), ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    legacy_owner_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    updated_by_user_id: Mapped[str | None] = mapped_column(String(ID_COLUMN_LENGTH), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    legacy_updated_by_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    channel = relationship("Channel")
    owner = relationship("User", foreign_keys=[owner_user_id])
    updated_by = relationship("User", foreign_keys=[updated_by_user_id])
    revisions = relationship("WorkspaceRevision", back_populates="document", cascade="all,delete-orphan")


class WorkspaceRevision(Base):
    __tablename__ = "workspace_revisions"

    id: Mapped[str] = mapped_column(String(ID_COLUMN_LENGTH), primary_key=True, index=True, default=lambda: generate_entity_id("workspace_revision"))
    legacy_id: Mapped[int | None] = mapped_column(Integer, nullable=True, unique=True, index=True)
    document_id: Mapped[str] = mapped_column(String(ID_COLUMN_LENGTH), ForeignKey("workspace_documents.id", ondelete="CASCADE"), nullable=False, index=True)
    legacy_document_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    editor_user_id: Mapped[str | None] = mapped_column(String(ID_COLUMN_LENGTH), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    legacy_editor_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    document = relationship("WorkspaceDocument", back_populates="revisions")
    editor = relationship("User")


class ServerActivityLog(Base):
    __tablename__ = "server_activity_logs"

    id: Mapped[str] = mapped_column(String(ID_COLUMN_LENGTH), primary_key=True, index=True, default=lambda: generate_entity_id("server_activity_log"))
    legacy_id: Mapped[int | None] = mapped_column(Integer, nullable=True, unique=True, index=True)
    server_id: Mapped[str | None] = mapped_column(String(ID_COLUMN_LENGTH), ForeignKey("servers.id", ondelete="CASCADE"), nullable=True, index=True)
    legacy_server_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    actor_user_id: Mapped[str | None] = mapped_column(String(ID_COLUMN_LENGTH), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    legacy_actor_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    target_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    activity_metadata: Mapped[dict | None] = mapped_column("metadata", json_value_type, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
