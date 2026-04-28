"""Add workspace visibility and ownership

Revision ID: 0009_add_workspace_visibility
Revises: 0008_add_community_tools
Create Date: 2026-04-09 10:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "0009_add_workspace_visibility"
down_revision = "0008_add_community_tools"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workspace_documents",
        sa.Column("visibility", sa.String(length=16), nullable=False, server_default=sa.text("'public'")),
    )
    op.add_column(
        "workspace_documents",
        sa.Column("owner_user_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        op.f("fk_workspace_documents_owner_user_id_users"),
        "workspace_documents",
        "users",
        ["owner_user_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(op.f("ix_workspace_documents_owner_user_id"), "workspace_documents", ["owner_user_id"], unique=False)

    op.drop_constraint(op.f("uq_workspace_documents_channel_id"), "workspace_documents", type_="unique")
    op.drop_index(op.f("ix_workspace_documents_channel_id"), table_name="workspace_documents")
    op.create_index(op.f("ix_workspace_documents_channel_id"), "workspace_documents", ["channel_id"], unique=False)
    op.create_index(
        "uq_workspace_documents_public_channel",
        "workspace_documents",
        ["channel_id"],
        unique=True,
        postgresql_where=sa.text("visibility = 'public' AND owner_user_id IS NULL"),
    )
    op.create_index(
        "uq_workspace_documents_private_channel_owner",
        "workspace_documents",
        ["channel_id", "owner_user_id"],
        unique=True,
        postgresql_where=sa.text("visibility = 'private' AND owner_user_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_workspace_documents_private_channel_owner", table_name="workspace_documents")
    op.drop_index("uq_workspace_documents_public_channel", table_name="workspace_documents")
    op.drop_index(op.f("ix_workspace_documents_owner_user_id"), table_name="workspace_documents")
    op.drop_constraint(op.f("fk_workspace_documents_owner_user_id_users"), "workspace_documents", type_="foreignkey")
    op.drop_index(op.f("ix_workspace_documents_channel_id"), table_name="workspace_documents")
    op.create_index(op.f("ix_workspace_documents_channel_id"), "workspace_documents", ["channel_id"], unique=True)
    op.create_unique_constraint(op.f("uq_workspace_documents_channel_id"), "workspace_documents", ["channel_id"])
    op.drop_column("workspace_documents", "owner_user_id")
    op.drop_column("workspace_documents", "visibility")
