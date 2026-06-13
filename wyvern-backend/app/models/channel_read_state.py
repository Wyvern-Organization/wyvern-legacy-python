from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.services.ids import ID_COLUMN_LENGTH, generate_entity_id


class ChannelReadState(Base):
    __tablename__ = "channel_read_states"
    __table_args__ = (
        UniqueConstraint("user_id", "channel_id", name="uq_channel_read_states_user_channel"),
    )

    id: Mapped[str] = mapped_column(
        String(ID_COLUMN_LENGTH),
        primary_key=True,
        index=True,
        default=lambda: generate_entity_id("channel_read_state"),
    )
    user_id: Mapped[str] = mapped_column(
        String(ID_COLUMN_LENGTH),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    channel_id: Mapped[str] = mapped_column(
        String(ID_COLUMN_LENGTH),
        ForeignKey("channels.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    last_read_message_id: Mapped[str | None] = mapped_column(
        String(ID_COLUMN_LENGTH),
        ForeignKey("messages.id", ondelete="SET NULL"),
        nullable=True,
    )
    last_read_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
