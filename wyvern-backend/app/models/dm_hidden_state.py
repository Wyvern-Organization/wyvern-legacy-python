from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.services.ids import ID_COLUMN_LENGTH


class DMHiddenState(Base):
    __tablename__ = "dm_hidden_states"

    channel_id: Mapped[str] = mapped_column(String(ID_COLUMN_LENGTH), ForeignKey("channels.id", ondelete="CASCADE"), primary_key=True)
    legacy_channel_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    user_id: Mapped[str] = mapped_column(String(ID_COLUMN_LENGTH), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    legacy_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    hidden_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    channel = relationship("Channel")
    user = relationship("User")
