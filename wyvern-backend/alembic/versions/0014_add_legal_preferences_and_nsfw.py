"""Add legal preferences and NSFW fields

Revision ID: 0014_legal_prefs_nsfw
Revises: 0013_recommendations
Create Date: 2026-05-22 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "0014_legal_prefs_nsfw"
down_revision = "0013_recommendations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("accepted_terms_version", sa.String(length=32), nullable=True))
    op.add_column("users", sa.Column("accepted_privacy_version", sa.String(length=32), nullable=True))
    op.add_column("users", sa.Column("legal_accepted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "users",
        sa.Column("ai_opt_in", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column("users", sa.Column("ai_opt_in_updated_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "users",
        sa.Column("nsfw_18_verified", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column("users", sa.Column("nsfw_18_verified_at", sa.DateTime(timezone=True), nullable=True))

    op.add_column(
        "messages",
        sa.Column("is_nsfw", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )


def downgrade() -> None:
    op.drop_column("messages", "is_nsfw")
    op.drop_column("users", "nsfw_18_verified_at")
    op.drop_column("users", "nsfw_18_verified")
    op.drop_column("users", "ai_opt_in_updated_at")
    op.drop_column("users", "ai_opt_in")
    op.drop_column("users", "legal_accepted_at")
    op.drop_column("users", "accepted_privacy_version")
    op.drop_column("users", "accepted_terms_version")
