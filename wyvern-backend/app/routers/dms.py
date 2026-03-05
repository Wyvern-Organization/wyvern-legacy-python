from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Channel, ChannelType, DMParticipant, User
from app.schemas.dm import DMChannelOut, DMCreateRequest, DMParticipantOut
from app.services.pubsub import publish_channel_event
from app.utils.dependencies import get_current_user
from app.utils.responses import success_response


router = APIRouter(prefix="/dms", tags=["dms"])


async def _serialize_dm_channel(db: AsyncSession, channel: Channel) -> DMChannelOut:
    result = await db.execute(
        select(User)
        .join(DMParticipant, DMParticipant.user_id == User.id)
        .where(DMParticipant.channel_id == channel.id)
        .order_by(User.id.asc())
    )
    participants = [
        DMParticipantOut(
            id=user.id,
            username=user.username,
            discriminator=user.discriminator,
            display_name=user.display_name,
            avatar=user.avatar,
        )
        for user in result.scalars().all()
    ]
    return DMChannelOut(
        id=channel.id,
        server_id=channel.server_id,
        name=channel.name,
        type=channel.type,
        position=channel.position,
        category=channel.category,
        created_by=channel.created_by,
        created_at=channel.created_at,
        participants=participants,
    )


@router.post("")
async def create_dm_channel(
    payload: DMCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    if payload.recipient_id == current_user.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot DM yourself")

    recipient = await db.execute(select(User).where(User.id == payload.recipient_id))
    if recipient.scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recipient not found")

    existing = await db.execute(
        select(Channel)
        .join(DMParticipant, DMParticipant.channel_id == Channel.id)
        .where(
            and_(
                Channel.type == ChannelType.dm,
                DMParticipant.user_id.in_([current_user.id, payload.recipient_id]),
            )
        )
        .group_by(Channel.id)
        .having(func.count(func.distinct(DMParticipant.user_id)) == 2)
    )
    channel = existing.scalars().first()
    if channel is not None:
        serialized = await _serialize_dm_channel(db, channel)
        return success_response(serialized.model_dump())

    channel = Channel(
        server_id=None,
        name=f"dm-{min(current_user.id, payload.recipient_id)}-{max(current_user.id, payload.recipient_id)}",
        type=ChannelType.dm,
        position=0,
        category=None,
        created_by=current_user.id,
    )
    db.add(channel)
    await db.flush()

    db.add_all(
        [
            DMParticipant(channel_id=channel.id, user_id=current_user.id),
            DMParticipant(channel_id=channel.id, user_id=payload.recipient_id),
        ]
    )

    await db.commit()
    await db.refresh(channel)

    serialized = await _serialize_dm_channel(db, channel)
    participant_ids = {current_user.id, payload.recipient_id}
    await publish_channel_event(
        channel.id,
        payload={
            "event": "dm.created",
            "channel_id": channel.id,
            "data": serialized.model_dump(mode="json"),
        },
        extra_user_ids=participant_ids,
    )
    return success_response(serialized.model_dump())


@router.get("")
async def list_dm_channels(db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict:
    result = await db.execute(
        select(Channel)
        .join(DMParticipant, DMParticipant.channel_id == Channel.id)
        .where(and_(Channel.type == ChannelType.dm, DMParticipant.user_id == current_user.id))
        .order_by(Channel.created_at.desc())
    )
    channels = result.scalars().all()
    payload = [await _serialize_dm_channel(db, channel) for channel in channels]
    return success_response([item.model_dump() for item in payload])


@router.get("/{channel_id}")
async def get_dm_channel(
    channel_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    result = await db.execute(select(Channel).where(and_(Channel.id == channel_id, Channel.type == ChannelType.dm)))
    channel = result.scalar_one_or_none()
    if channel is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="DM channel not found")

    participant = await db.execute(
        select(DMParticipant).where(
            and_(DMParticipant.channel_id == channel_id, DMParticipant.user_id == current_user.id)
        )
    )
    if participant.scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No DM access")

    serialized = await _serialize_dm_channel(db, channel)
    return success_response(serialized.model_dump())


@router.delete("/{channel_id}")
async def close_dm_channel(
    channel_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    result = await db.execute(select(Channel).where(and_(Channel.id == channel_id, Channel.type == ChannelType.dm)))
    channel = result.scalar_one_or_none()
    if channel is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="DM channel not found")

    participant = await db.execute(
        select(DMParticipant).where(
            and_(DMParticipant.channel_id == channel_id, DMParticipant.user_id == current_user.id)
        )
    )
    if participant.scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No DM access")

    participant_ids_result = await db.execute(select(DMParticipant.user_id).where(DMParticipant.channel_id == channel_id))
    participant_ids = set(participant_ids_result.scalars().all())

    await db.delete(channel)
    await db.commit()

    if participant_ids:
        await publish_channel_event(
            channel_id,
            payload={
                "event": "dm.deleted",
                "channel_id": channel_id,
                "data": {"id": channel_id},
            },
            extra_user_ids=participant_ids,
        )
    return success_response({"deleted": True})
