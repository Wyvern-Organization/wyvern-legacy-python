from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.enums import ChannelType
from app.models.sync import SyncMixin
from app.services.ids import ID_COLUMN_LENGTH, generate_entity_id


class Channel(SyncMixin, Base):
    __tablename__ = "channels"

    id: Mapped[str] = mapped_column(String(ID_COLUMN_LENGTH), primary_key=True, index=True, default=lambda: generate_entity_id("channel"))
    legacy_id: Mapped[int | None] = mapped_column(Integer, nullable=True, unique=True, index=True)
    server_id: Mapped[str | None] = mapped_column(String(ID_COLUMN_LENGTH), ForeignKey("servers.id", ondelete="CASCADE"), nullable=True, index=True)
    legacy_server_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    type: Mapped[ChannelType] = mapped_column(Enum(ChannelType, name="channel_type"), nullable=False, default=ChannelType.text)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    category: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_by: Mapped[str] = mapped_column(String(ID_COLUMN_LENGTH), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    legacy_created_by: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    server = relationship("Server", back_populates="channels")
    messages = relationship("Message", back_populates="channel", cascade="all,delete-orphan")
    participants = relationship("DMParticipant", back_populates="channel", cascade="all,delete-orphan")
