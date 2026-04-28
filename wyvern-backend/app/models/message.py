from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.sync import SyncMixin
from app.services.ids import ID_COLUMN_LENGTH, generate_entity_id


class Message(SyncMixin, Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(ID_COLUMN_LENGTH), primary_key=True, index=True, default=lambda: generate_entity_id("message"))
    legacy_id: Mapped[int | None] = mapped_column(Integer, nullable=True, unique=True, index=True)
    channel_id: Mapped[str] = mapped_column(String(ID_COLUMN_LENGTH), ForeignKey("channels.id", ondelete="CASCADE"), nullable=False, index=True)
    legacy_channel_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    author_id: Mapped[str] = mapped_column(String(ID_COLUMN_LENGTH), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    legacy_author_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    reply_to_id: Mapped[str | None] = mapped_column(
        String(ID_COLUMN_LENGTH),
        ForeignKey("messages.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    legacy_reply_to_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    attachments: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    is_pinned: Mapped[bool] = mapped_column(default=False, nullable=False, server_default="false")
    webhook_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    webhook_avatar: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    channel = relationship("Channel", back_populates="messages")
    author = relationship("User", back_populates="messages")
    reply_to = relationship("Message", remote_side=[id], foreign_keys=[reply_to_id], back_populates="replies")
    replies = relationship("Message", back_populates="reply_to")
    reactions = relationship("Reaction", back_populates="message", cascade="all,delete-orphan")
