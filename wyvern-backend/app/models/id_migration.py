from datetime import datetime

from sqlalchemy import DateTime, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.services.ids import ID_COLUMN_LENGTH, generate_entity_id


class IdMigrationMap(Base):
    __tablename__ = "id_migration_map"
    __table_args__ = (
        UniqueConstraint("entity_type", "legacy_id", name="uq_id_migration_map_entity_legacy"),
        UniqueConstraint("new_id", name="uq_id_migration_map_new_id"),
    )

    id: Mapped[str] = mapped_column(String(ID_COLUMN_LENGTH), primary_key=True, default=lambda: generate_entity_id("id_migration_map"))
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    table_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    legacy_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    new_id: Mapped[str] = mapped_column(String(ID_COLUMN_LENGTH), nullable=False, index=True)
    migrated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
