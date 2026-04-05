from datetime import datetime
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from app.database import Base


def generate_sync_id() -> str:
    return str(uuid4())


class SyncMixin:
    @declared_attr.directive
    def sync_id(cls) -> Mapped[str]:
        return mapped_column(String(36), unique=True, nullable=False, index=True, default=generate_sync_id)

    @declared_attr.directive
    def sync_version(cls) -> Mapped[int]:
        return mapped_column(Integer, nullable=False, default=1, server_default="1")


class ReplicationOutbox(Base):
    __tablename__ = "replication_outbox"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    event_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True, index=True)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    source_node: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    entity_sync_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    base_sync_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dead_letter: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class ReplicationInboundLedger(Base):
    __tablename__ = "replication_inbound_ledger"

    event_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source_node: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    entity_sync_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
