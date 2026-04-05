"""Add edge sync bridge metadata and tables

Revision ID: 0006_add_edge_sync_bridge
Revises: 0005_add_message_replies
Create Date: 2026-04-05 03:40:00.000000
"""

from uuid import uuid4

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0006_add_edge_sync_bridge"
down_revision = "0005_add_message_replies"
branch_labels = None
depends_on = None


SYNC_TABLE_KEYS: dict[str, tuple[str, ...]] = {
    "users": ("id",),
    "servers": ("id",),
    "channels": ("id",),
    "server_members": ("server_id", "user_id"),
    "server_invites": ("id",),
    "dm_participants": ("channel_id", "user_id"),
    "messages": ("id",),
    "reactions": ("message_id", "user_id", "emoji"),
}


def _backfill_sync_ids(table_name: str, key_columns: tuple[str, ...]) -> None:
    bind = op.get_bind()
    table = sa.table(
        table_name,
        sa.column("sync_id", sa.String(length=36)),
        *(sa.column(column_name) for column_name in key_columns),
    )
    rows = bind.execute(sa.select(*(getattr(table.c, column_name) for column_name in key_columns))).all()

    for row in rows:
        key_filters = []
        for column_name in key_columns:
            key_filters.append(getattr(table.c, column_name) == getattr(row, column_name))
        bind.execute(
            table.update().where(sa.and_(*key_filters)).values(sync_id=str(uuid4()))
        )


def upgrade() -> None:
    for table_name in SYNC_TABLE_KEYS:
        op.add_column(table_name, sa.Column("sync_id", sa.String(length=36), nullable=True))
        op.add_column(
            table_name,
            sa.Column("sync_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        )
        _backfill_sync_ids(table_name, SYNC_TABLE_KEYS[table_name])
        op.alter_column(table_name, "sync_id", existing_type=sa.String(length=36), nullable=False)
        op.create_index(op.f(f"ix_{table_name}_sync_id"), table_name, ["sync_id"], unique=True)

    op.create_table(
        "replication_outbox",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("event_id", sa.String(length=36), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("source_node", sa.String(length=16), nullable=False),
        sa.Column("entity_type", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("entity_sync_id", sa.String(length=36), nullable=False),
        sa.Column("base_sync_version", sa.Integer(), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dead_letter", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_replication_outbox")),
    )
    op.create_index(op.f("ix_replication_outbox_id"), "replication_outbox", ["id"], unique=False)
    op.create_index(op.f("ix_replication_outbox_event_id"), "replication_outbox", ["event_id"], unique=True)
    op.create_index(op.f("ix_replication_outbox_source_node"), "replication_outbox", ["source_node"], unique=False)
    op.create_index(op.f("ix_replication_outbox_entity_type"), "replication_outbox", ["entity_type"], unique=False)
    op.create_index(op.f("ix_replication_outbox_entity_sync_id"), "replication_outbox", ["entity_sync_id"], unique=False)

    op.create_table(
        "replication_inbound_ledger",
        sa.Column("event_id", sa.String(length=36), nullable=False),
        sa.Column("source_node", sa.String(length=16), nullable=False),
        sa.Column("entity_type", sa.String(length=64), nullable=False),
        sa.Column("entity_sync_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("event_id", name=op.f("pk_replication_inbound_ledger")),
    )
    op.create_index(
        op.f("ix_replication_inbound_ledger_source_node"),
        "replication_inbound_ledger",
        ["source_node"],
        unique=False,
    )
    op.create_index(
        op.f("ix_replication_inbound_ledger_entity_type"),
        "replication_inbound_ledger",
        ["entity_type"],
        unique=False,
    )
    op.create_index(
        op.f("ix_replication_inbound_ledger_entity_sync_id"),
        "replication_inbound_ledger",
        ["entity_sync_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_replication_inbound_ledger_entity_sync_id"), table_name="replication_inbound_ledger")
    op.drop_index(op.f("ix_replication_inbound_ledger_entity_type"), table_name="replication_inbound_ledger")
    op.drop_index(op.f("ix_replication_inbound_ledger_source_node"), table_name="replication_inbound_ledger")
    op.drop_table("replication_inbound_ledger")

    op.drop_index(op.f("ix_replication_outbox_entity_sync_id"), table_name="replication_outbox")
    op.drop_index(op.f("ix_replication_outbox_entity_type"), table_name="replication_outbox")
    op.drop_index(op.f("ix_replication_outbox_source_node"), table_name="replication_outbox")
    op.drop_index(op.f("ix_replication_outbox_event_id"), table_name="replication_outbox")
    op.drop_index(op.f("ix_replication_outbox_id"), table_name="replication_outbox")
    op.drop_table("replication_outbox")

    for table_name in reversed(tuple(SYNC_TABLE_KEYS.keys())):
        op.drop_index(op.f(f"ix_{table_name}_sync_id"), table_name=table_name)
        op.drop_column(table_name, "sync_version")
        op.drop_column(table_name, "sync_id")
