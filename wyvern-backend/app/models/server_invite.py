from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.sync import SyncMixin
from app.services.ids import ID_COLUMN_LENGTH, generate_entity_id


class ServerInvite(SyncMixin, Base):
    __tablename__ = "server_invites"

    id: Mapped[str] = mapped_column(String(ID_COLUMN_LENGTH), primary_key=True, index=True, default=lambda: generate_entity_id("server_invite"))
    legacy_id: Mapped[int | None] = mapped_column(Integer, nullable=True, unique=True, index=True)
    server_id: Mapped[str] = mapped_column(String(ID_COLUMN_LENGTH), ForeignKey("servers.id", ondelete="CASCADE"), nullable=False, index=True)
    legacy_server_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True, index=True)
    created_by: Mapped[str] = mapped_column(String(ID_COLUMN_LENGTH), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    legacy_created_by: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    server = relationship("Server", back_populates="invites")
