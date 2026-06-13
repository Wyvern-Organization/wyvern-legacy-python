from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.json_types import json_value_type
from app.services.ids import ID_COLUMN_LENGTH, generate_entity_id


class RecommendationEmbedding(Base):
    __tablename__ = "recommendation_embeddings"
    __table_args__ = (
        UniqueConstraint("entity_type", "entity_id", "embedding_model", name="uq_recommendation_embeddings_entity_model"),
    )

    id: Mapped[str] = mapped_column(
        String(ID_COLUMN_LENGTH),
        primary_key=True,
        index=True,
        default=lambda: generate_entity_id("recommendation_embedding"),
    )
    entity_type: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    entity_id: Mapped[str] = mapped_column(String(ID_COLUMN_LENGTH), nullable=False, index=True)
    embedding_model: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(json_value_type, nullable=True)
    stale: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    embedded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class UserRecommendation(Base):
    __tablename__ = "user_recommendations"
    __table_args__ = (
        UniqueConstraint("user_id", "target_type", "target_id", name="uq_user_recommendations_user_target"),
    )

    id: Mapped[str] = mapped_column(
        String(ID_COLUMN_LENGTH),
        primary_key=True,
        index=True,
        default=lambda: generate_entity_id("user_recommendation"),
    )
    user_id: Mapped[str] = mapped_column(String(ID_COLUMN_LENGTH), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    target_type: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    target_id: Mapped[str] = mapped_column(String(ID_COLUMN_LENGTH), nullable=False, index=True)
    score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default="0")
    reason: Mapped[str | None] = mapped_column(String(80), nullable=True)
    factors: Mapped[dict | None] = mapped_column(json_value_type, nullable=True)
    rank: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    refreshed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    user = relationship("User")


class RecommendationSignal(Base):
    __tablename__ = "recommendation_signals"
    __table_args__ = (
        UniqueConstraint("user_id", "target_type", "target_id", "action", name="uq_recommendation_signals_user_target_action"),
    )

    id: Mapped[str] = mapped_column(
        String(ID_COLUMN_LENGTH),
        primary_key=True,
        index=True,
        default=lambda: generate_entity_id("recommendation_signal"),
    )
    user_id: Mapped[str] = mapped_column(String(ID_COLUMN_LENGTH), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    target_type: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    target_id: Mapped[str] = mapped_column(String(ID_COLUMN_LENGTH), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    weight: Mapped[float] = mapped_column(Float, nullable=False, default=1.0, server_default="1")
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    user = relationship("User")
