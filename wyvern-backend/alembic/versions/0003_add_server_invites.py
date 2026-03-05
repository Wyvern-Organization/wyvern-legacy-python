"""Add server invites

Revision ID: 0003_add_server_invites
Revises: 0002_add_user_display_name
Create Date: 2026-03-04 05:30:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "0003_add_server_invites"
down_revision = "0002_add_user_display_name"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "server_invites",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("server_id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(
            ["server_id"], ["servers.id"], name=op.f("fk_server_invites_server_id_servers"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_server_invites_created_by_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_server_invites")),
        sa.UniqueConstraint("code", name=op.f("uq_server_invites_code")),
    )
    op.create_index(op.f("ix_server_invites_id"), "server_invites", ["id"], unique=False)
    op.create_index(op.f("ix_server_invites_server_id"), "server_invites", ["server_id"], unique=False)
    op.create_index(op.f("ix_server_invites_code"), "server_invites", ["code"], unique=False)
    op.create_index(op.f("ix_server_invites_created_by"), "server_invites", ["created_by"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_server_invites_created_by"), table_name="server_invites")
    op.drop_index(op.f("ix_server_invites_code"), table_name="server_invites")
    op.drop_index(op.f("ix_server_invites_server_id"), table_name="server_invites")
    op.drop_index(op.f("ix_server_invites_id"), table_name="server_invites")
    op.drop_table("server_invites")
