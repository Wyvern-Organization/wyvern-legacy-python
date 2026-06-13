from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.services.ids import ID_COLUMN_LENGTH, generate_entity_id


class UiVariantVote(Base):
    __tablename__ = "ui_variant_votes"
    __table_args__ = (
        UniqueConstraint("user_id", "poll_key", name="uq_ui_variant_votes_user_poll"),
    )

    id: Mapped[str] = mapped_column(
        String(ID_COLUMN_LENGTH),
        primary_key=True,
        index=True,
        default=lambda: generate_entity_id("ui_variant_vote"),
    )
    user_id: Mapped[str] = mapped_column(
        String(ID_COLUMN_LENGTH),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    poll_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    variant_key: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
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
