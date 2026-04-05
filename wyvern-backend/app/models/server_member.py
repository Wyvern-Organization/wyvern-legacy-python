from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.enums import MemberRole
from app.models.sync import SyncMixin


class ServerMember(SyncMixin, Base):
    __tablename__ = "server_members"

    server_id: Mapped[int] = mapped_column(ForeignKey("servers.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    role: Mapped[MemberRole] = mapped_column(Enum(MemberRole, name="member_role"), nullable=False, default=MemberRole.member)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    server = relationship("Server", back_populates="members")
    user = relationship("User", back_populates="memberships")
