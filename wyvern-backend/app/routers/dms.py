from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import and_, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Channel, ChannelType, DMHiddenState, DMParticipant, User
from app.schemas.dm import DMChannelOut, DMCreateRequest, DMParticipantOut
from app.services.pubsub import publish_channel_event, publish_user_event
from app.services.recommendations import TARGET_USER, record_recommendation_signal
from app.services.sync_bridge import enqueue_upsert_event
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

    recipient_result = await db.execute(select(User).where(User.id == payload.recipient_id))
    recipient = recipient_result.scalar_one_or_none()
    if recipient is None:
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
        await db.execute(
            delete(DMHiddenState).where(
                and_(DMHiddenState.channel_id == channel.id, DMHiddenState.user_id == current_user.id)
            )
        )
        await record_recommendation_signal(db, current_user.id, TARGET_USER, recipient.id, "dm.opened", weight=1.5)
        await db.commit()
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

    participants = [
        DMParticipant(channel_id=channel.id, user_id=current_user.id),
        DMParticipant(channel_id=channel.id, user_id=payload.recipient_id),
    ]
    db.add_all(
        participants
    )
    await db.flush()
    await enqueue_upsert_event(db, "channel", channel, base_sync_version=0)
    for participant in participants:
        await enqueue_upsert_event(db, "dm_participant", participant, base_sync_version=0)
    await record_recommendation_signal(db, current_user.id, TARGET_USER, recipient.id, "dm.created", weight=2.0)

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
        .outerjoin(
            DMHiddenState,
            and_(DMHiddenState.channel_id == Channel.id, DMHiddenState.user_id == current_user.id),
        )
        .where(
            and_(
                Channel.type == ChannelType.dm,
                DMParticipant.user_id == current_user.id,
                DMHiddenState.user_id.is_(None),
            )
        )
        .order_by(Channel.created_at.desc())
    )
    channels = result.scalars().all()
    payload = [await _serialize_dm_channel(db, channel) for channel in channels]
    return success_response([item.model_dump() for item in payload])


@router.get("/{channel_id}")
async def get_dm_channel(
    channel_id: str,
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

    hidden_state = await db.execute(
        select(DMHiddenState).where(
            and_(DMHiddenState.channel_id == channel_id, DMHiddenState.user_id == current_user.id)
        )
    )
    if hidden_state.scalar_one_or_none() is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="DM channel not found")

    serialized = await _serialize_dm_channel(db, channel)
    return success_response(serialized.model_dump())


@router.delete("/{channel_id}")
async def close_dm_channel(
    channel_id: str,
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

    hidden_state = await db.execute(
        select(DMHiddenState).where(
            and_(DMHiddenState.channel_id == channel_id, DMHiddenState.user_id == current_user.id)
        )
    )
    if hidden_state.scalar_one_or_none() is None:
        db.add(DMHiddenState(channel_id=channel_id, user_id=current_user.id))
    await db.commit()

    await publish_user_event(
        {current_user.id},
        payload={
            "event": "dm.deleted",
            "channel_id": channel_id,
            "data": {"id": channel_id},
        },
    )
    return success_response({"deleted": True})
