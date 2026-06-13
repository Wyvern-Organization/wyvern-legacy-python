from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.json_types import json_value_type


class OAuthClientRegistration(Base):
    __tablename__ = "oauth_client_registrations"

    client_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    payload: Mapped[dict] = mapped_column(json_value_type, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
