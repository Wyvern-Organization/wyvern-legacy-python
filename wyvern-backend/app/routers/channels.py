from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Channel, ChannelType, MemberRole, ServerMember, User
from app.schemas.channel import ChannelCreate, ChannelOut, ChannelUpdate
from app.services.community import record_server_activity
from app.services.recommendations import TARGET_SERVER, mark_public_entity_stale
from app.services.realtime import broadcast_channel_event
from app.services.sync_bridge import bump_sync_version, enqueue_delete_event, enqueue_upsert_event
from app.utils.dependencies import get_current_user
from app.utils.responses import success_response


router = APIRouter(prefix="/channels", tags=["channels"])


def _can_manage_channels(role: MemberRole) -> bool:
    return role in {MemberRole.owner, MemberRole.admin, MemberRole.moderator}


async def _require_server_membership(db: AsyncSession, server_id: str, user_id: str) -> ServerMember:
    result = await db.execute(
        select(ServerMember).where(and_(ServerMember.server_id == server_id, ServerMember.user_id == user_id))
    )
    membership = result.scalar_one_or_none()
    if membership is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not a server member")
    return membership


@router.post("/server/{server_id}")
async def create_channel(
    server_id: str,
    payload: ChannelCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    membership = await _require_server_membership(db, server_id, current_user.id)
    if not _can_manage_channels(membership.role):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")

    if payload.type == ChannelType.dm:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="DM channels must use DM endpoints")

    channel = Channel(
        server_id=server_id,
        name=payload.name,
        type=payload.type,
        position=payload.position,
        category=payload.category,
        created_by=current_user.id,
    )
    db.add(channel)
    await db.flush()
    await enqueue_upsert_event(db, "channel", channel, base_sync_version=0)
    await mark_public_entity_stale(db, TARGET_SERVER, server_id)
    await record_server_activity(
        db,
        server_id=server_id,
        actor_user_id=current_user.id,
        action="channel.created",
        target_type="channel",
        target_id=str(channel.id),
        metadata={"name": channel.name, "type": channel.type.value if hasattr(channel.type, "value") else str(channel.type)},
    )
    await db.commit()
    await db.refresh(channel)
    await broadcast_channel_event(db, "created", channel)

    return success_response(ChannelOut.model_validate(channel).model_dump())


@router.get("/server/{server_id}")
async def list_server_channels(
    server_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    await _require_server_membership(db, server_id, current_user.id)

    result = await db.execute(
        select(Channel)
        .where(and_(Channel.server_id == server_id, Channel.type.in_([ChannelType.text, ChannelType.voice])))
        .order_by(Channel.position.asc(), Channel.created_at.asc())
    )
    channels = result.scalars().all()

    return success_response([ChannelOut.model_validate(channel).model_dump() for channel in channels])


@router.get("/{channel_id}")
async def get_channel(
    channel_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    result = await db.execute(select(Channel).where(Channel.id == channel_id))
    channel = result.scalar_one_or_none()
    if channel is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Channel not found")

    if channel.type != ChannelType.dm:
        if channel.server_id is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid server channel")
        await _require_server_membership(db, channel.server_id, current_user.id)

    return success_response(ChannelOut.model_validate(channel).model_dump())


@router.patch("/{channel_id}")
async def update_channel(
    channel_id: str,
    payload: ChannelUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    result = await db.execute(select(Channel).where(Channel.id == channel_id))
    channel = result.scalar_one_or_none()
    if channel is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Channel not found")

    if channel.type == ChannelType.dm or channel.server_id is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Use DM routes for direct messages")

    membership = await _require_server_membership(db, channel.server_id, current_user.id)
    if not _can_manage_channels(membership.role):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")

    if payload.name is not None:
        channel.name = payload.name
    if payload.position is not None:
        channel.position = payload.position
    if payload.category is not None:
        channel.category = payload.category
    base_sync_version = bump_sync_version(channel)
    await enqueue_upsert_event(db, "channel", channel, base_sync_version=base_sync_version)
    if payload.name is not None:
        await mark_public_entity_stale(db, TARGET_SERVER, channel.server_id)
    await record_server_activity(
        db,
        server_id=channel.server_id,
        actor_user_id=current_user.id,
        action="channel.updated",
        target_type="channel",
        target_id=str(channel.id),
        metadata={"name": channel.name, "position": channel.position, "category": channel.category},
    )

    await db.commit()
    await db.refresh(channel)
    await broadcast_channel_event(db, "updated", channel)
    return success_response(ChannelOut.model_validate(channel).model_dump())


@router.delete("/{channel_id}")
async def delete_channel(
    channel_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    result = await db.execute(select(Channel).where(Channel.id == channel_id))
    channel = result.scalar_one_or_none()
    if channel is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Channel not found")

    if channel.type == ChannelType.dm or channel.server_id is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Use DM routes for direct messages")

    membership = await _require_server_membership(db, channel.server_id, current_user.id)
    if not _can_manage_channels(membership.role):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")

    recipient_ids_result = await db.execute(select(ServerMember.user_id).where(ServerMember.server_id == channel.server_id))
    recipient_ids = {str(user_id) for user_id in recipient_ids_result.scalars().all()}
    await enqueue_delete_event(db, "channel", channel, base_sync_version=channel.sync_version)
    await mark_public_entity_stale(db, TARGET_SERVER, channel.server_id)
    await record_server_activity(
        db,
        server_id=channel.server_id,
        actor_user_id=current_user.id,
        action="channel.deleted",
        target_type="channel",
        target_id=str(channel.id),
        metadata={"name": channel.name},
    )
    await db.delete(channel)
    await db.commit()
    await broadcast_channel_event(db, "deleted", channel, recipients=recipient_ids)
    return success_response({"deleted": True})
