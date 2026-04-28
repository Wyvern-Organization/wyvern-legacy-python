from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.enums import MemberRole
from app.models.sync import SyncMixin
from app.services.ids import ID_COLUMN_LENGTH


class ServerMember(SyncMixin, Base):
    __tablename__ = "server_members"

    server_id: Mapped[str] = mapped_column(String(ID_COLUMN_LENGTH), ForeignKey("servers.id", ondelete="CASCADE"), primary_key=True)
    legacy_server_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    user_id: Mapped[str] = mapped_column(String(ID_COLUMN_LENGTH), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    legacy_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    role: Mapped[MemberRole] = mapped_column(Enum(MemberRole, name="member_role"), nullable=False, default=MemberRole.member)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    server = relationship("Server", back_populates="members")
    user = relationship("User", back_populates="memberships")
