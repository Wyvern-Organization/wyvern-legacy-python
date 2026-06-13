"""Add Edge UI variant votes

Revision ID: 0015_ui_variant_votes
Revises: 0014_legal_prefs_nsfw
Create Date: 2026-05-24 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

from app.services.ids import ID_COLUMN_LENGTH


revision = "0015_ui_variant_votes"
down_revision = "0014_legal_prefs_nsfw"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ui_variant_votes",
        sa.Column("id", sa.String(length=ID_COLUMN_LENGTH), nullable=False),
        sa.Column("user_id", sa.String(length=ID_COLUMN_LENGTH), nullable=False),
        sa.Column("poll_key", sa.String(length=64), nullable=False),
        sa.Column("variant_key", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_ui_variant_votes_user_id_users"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ui_variant_votes")),
        sa.UniqueConstraint("user_id", "poll_key", name="uq_ui_variant_votes_user_poll"),
    )
    op.create_index(op.f("ix_ui_variant_votes_id"), "ui_variant_votes", ["id"], unique=False)
    op.create_index(op.f("ix_ui_variant_votes_user_id"), "ui_variant_votes", ["user_id"], unique=False)
    op.create_index(op.f("ix_ui_variant_votes_poll_key"), "ui_variant_votes", ["poll_key"], unique=False)
    op.create_index(op.f("ix_ui_variant_votes_variant_key"), "ui_variant_votes", ["variant_key"], unique=False)


def downgrade() -> None:
    op.drop_table("ui_variant_votes")
