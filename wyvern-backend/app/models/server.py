from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.sync import SyncMixin
from app.services.ids import ID_COLUMN_LENGTH, generate_entity_id


class Server(SyncMixin, Base):
    __tablename__ = "servers"

    id: Mapped[str] = mapped_column(String(ID_COLUMN_LENGTH), primary_key=True, index=True, default=lambda: generate_entity_id("server"))
    legacy_id: Mapped[int | None] = mapped_column(Integer, nullable=True, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(String(512), nullable=True)
    icon: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    directory_opt_in: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    owner_id: Mapped[str] = mapped_column(String(ID_COLUMN_LENGTH), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    legacy_owner_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    owner = relationship("User", back_populates="owned_servers")
    members = relationship("ServerMember", back_populates="server", cascade="all,delete-orphan")
    channels = relationship("Channel", back_populates="server", cascade="all,delete-orphan")
    invites = relationship("ServerInvite", back_populates="server", cascade="all,delete-orphan")
