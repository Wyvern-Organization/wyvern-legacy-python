from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.services.ids import ID_COLUMN_LENGTH, generate_entity_id


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[str] = mapped_column(String(ID_COLUMN_LENGTH), primary_key=True, default=lambda: generate_entity_id("refresh_token"))
    legacy_id: Mapped[int | None] = mapped_column(Integer, nullable=True, unique=True, index=True)
    user_id: Mapped[str] = mapped_column(String(ID_COLUMN_LENGTH), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    legacy_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    token_hash: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    is_revoked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    user = relationship("User", back_populates="refresh_tokens")
