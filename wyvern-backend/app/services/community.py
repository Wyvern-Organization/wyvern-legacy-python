from __future__ import annotations

import hashlib
import secrets
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ServerActivityLog, ServerMember
from app.schemas.community import CommunityActivityOut
from app.services.pubsub import publish_user_event


def generate_secret_token() -> str:
    return secrets.token_urlsafe(32)


def hash_secret_token(token: str) -> str:
    normalized = (token or "").strip().encode("utf-8")
    return hashlib.sha256(normalized).hexdigest()


async def record_server_activity(
    db: AsyncSession,
    *,
    server_id: str | None,
    actor_user_id: str | None,
    action: str,
    target_type: str | None = None,
    target_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> ServerActivityLog:
    entry = ServerActivityLog(
        server_id=server_id,
        actor_user_id=actor_user_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        activity_metadata=metadata,
    )
    db.add(entry)
    await db.flush()
    if server_id:
        result = await db.execute(select(ServerMember.user_id).where(ServerMember.server_id == server_id))
        recipients = {str(user_id) for user_id in result.scalars().all()}
        await publish_user_event(
            recipients,
            {
                "event": "server.activity.created",
                "data": CommunityActivityOut.model_validate(entry).model_dump(mode="json"),
            },
        )
    return entry
