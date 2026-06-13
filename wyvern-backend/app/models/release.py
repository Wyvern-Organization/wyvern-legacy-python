from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.json_types import json_value_type
from app.models.sync import SyncMixin
from app.services.ids import ID_COLUMN_LENGTH, generate_entity_id


class ReleaseFlag(SyncMixin, Base):
    __tablename__ = "release_flags"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    stable_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    edge_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    updated_by_user_id: Mapped[str | None] = mapped_column(
        String(ID_COLUMN_LENGTH),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    legacy_updated_by_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    last_promoted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ReleasePromotionAudit(Base):
    __tablename__ = "release_promotion_audit"

    id: Mapped[str] = mapped_column(String(ID_COLUMN_LENGTH), primary_key=True, index=True, default=lambda: generate_entity_id("release_promotion_audit"))
    legacy_id: Mapped[int | None] = mapped_column(Integer, nullable=True, unique=True, index=True)
    promoted_by_user_id: Mapped[str | None] = mapped_column(
        String(ID_COLUMN_LENGTH),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    legacy_promoted_by_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    promoted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    promoted_flag_keys: Mapped[list[str]] = mapped_column(
        json_value_type,
        nullable=False,
        default=list,
        server_default=text("'[]'"),
    )
    stable_snapshot: Mapped[dict | None] = mapped_column(json_value_type, nullable=True)
