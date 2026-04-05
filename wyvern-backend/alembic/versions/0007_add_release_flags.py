"""Add release flags and promotion audit tables

Revision ID: 0007_add_release_flags
Revises: 0006_add_edge_sync_bridge
Create Date: 2026-04-05 04:25:00.000000
"""

from uuid import uuid4

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0007_add_release_flags"
down_revision = "0006_add_edge_sync_bridge"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "release_flags",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("sync_id", sa.String(length=36), nullable=False),
        sa.Column("sync_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("stable_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("edge_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("updated_by_user_id", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_promoted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_release_flags")),
        sa.ForeignKeyConstraint(["updated_by_user_id"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index(op.f("ix_release_flags_sync_id"), "release_flags", ["sync_id"], unique=True)
    op.create_index(op.f("ix_release_flags_updated_by_user_id"), "release_flags", ["updated_by_user_id"], unique=False)

    release_flags = sa.table(
        "release_flags",
        sa.column("key", sa.String(length=64)),
        sa.column("sync_id", sa.String(length=36)),
        sa.column("sync_version", sa.Integer()),
        sa.column("description", sa.Text()),
        sa.column("stable_enabled", sa.Boolean()),
        sa.column("edge_enabled", sa.Boolean()),
        sa.column("updated_by_user_id", sa.Integer()),
        sa.column("last_promoted_at", sa.DateTime(timezone=True)),
    )
    op.bulk_insert(
        release_flags,
        [
            {
                "key": "edge_release_banner",
                "sync_id": str(uuid4()),
                "sync_version": 1,
                "description": "Show the Edge release banner inside the Edge app shell.",
                "stable_enabled": False,
                "edge_enabled": True,
                "updated_by_user_id": None,
                "last_promoted_at": None,
            }
        ],
    )

    op.create_table(
        "release_promotion_audit",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("promoted_by_user_id", sa.Integer(), nullable=True),
        sa.Column("promoted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("promoted_flag_keys", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("stable_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_release_promotion_audit")),
        sa.ForeignKeyConstraint(["promoted_by_user_id"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index(op.f("ix_release_promotion_audit_id"), "release_promotion_audit", ["id"], unique=False)
    op.create_index(
        op.f("ix_release_promotion_audit_promoted_by_user_id"),
        "release_promotion_audit",
        ["promoted_by_user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_release_promotion_audit_promoted_at"),
        "release_promotion_audit",
        ["promoted_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_release_promotion_audit_promoted_at"), table_name="release_promotion_audit")
    op.drop_index(op.f("ix_release_promotion_audit_promoted_by_user_id"), table_name="release_promotion_audit")
    op.drop_index(op.f("ix_release_promotion_audit_id"), table_name="release_promotion_audit")
    op.drop_table("release_promotion_audit")

    op.drop_index(op.f("ix_release_flags_updated_by_user_id"), table_name="release_flags")
    op.drop_index(op.f("ix_release_flags_sync_id"), table_name="release_flags")
    op.drop_table("release_flags")
