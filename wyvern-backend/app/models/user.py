from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.sync import SyncMixin


class User(SyncMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("username", "discriminator", name="uq_users_username_discriminator"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    username: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    discriminator: Mapped[str] = mapped_column(String(4), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    bio: Mapped[str | None] = mapped_column(String(280), nullable=True)
    directory_opt_in: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    avatar: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_paid: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    owned_servers = relationship("Server", back_populates="owner", cascade="all,delete")
    memberships = relationship("ServerMember", back_populates="user", cascade="all,delete")
    messages = relationship("Message", back_populates="author", cascade="all,delete")
    reactions = relationship("Reaction", back_populates="user", cascade="all,delete")
    refresh_tokens = relationship("RefreshToken", back_populates="user", cascade="all,delete")
    dm_participations = relationship("DMParticipant", back_populates="user", cascade="all,delete")
