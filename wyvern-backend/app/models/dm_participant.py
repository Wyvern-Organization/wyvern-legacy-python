from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.sync import SyncMixin
from app.services.ids import ID_COLUMN_LENGTH


class DMParticipant(SyncMixin, Base):
    __tablename__ = "dm_participants"

    channel_id: Mapped[str] = mapped_column(String(ID_COLUMN_LENGTH), ForeignKey("channels.id", ondelete="CASCADE"), primary_key=True)
    legacy_channel_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    user_id: Mapped[str] = mapped_column(String(ID_COLUMN_LENGTH), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    legacy_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    channel = relationship("Channel", back_populates="participants")
    user = relationship("User", back_populates="dm_participations")
