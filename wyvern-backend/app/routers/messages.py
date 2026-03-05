from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.models import Channel, ChannelType, DMParticipant, MemberRole, Message, Reaction, ServerMember, User
from app.schemas.message import MessageCreate, MessageHistoryOut, MessageOut, MessageUpdate, ReactionPayload
from app.services.access import ensure_channel_access, get_channel_or_404, get_message_or_404
from app.services.pubsub import publish_channel_event
from app.services.rate_limiter import rate_limiter
from app.utils.dependencies import get_current_user
from app.utils.responses import success_response


router = APIRouter(prefix="/messages", tags=["messages"])
settings = get_settings()


def _can_moderate(role: MemberRole) -> bool:
    return role in {MemberRole.owner, MemberRole.admin, MemberRole.moderator}


async def _server_membership_for_channel(db: AsyncSession, message: Message, user_id: int) -> ServerMember | None:
    channel = await get_channel_or_404(db, message.channel_id)
    if channel.type == ChannelType.dm or channel.server_id is None:
        return None

    result = await db.execute(
        select(ServerMember).where(
            and_(ServerMember.server_id == channel.server_id, ServerMember.user_id == user_id)
        )
    )
    return result.scalar_one_or_none()


async def _channel_member_ids(db: AsyncSession, channel: Channel) -> set[int]:
    if channel.type == ChannelType.dm:
        result = await db.execute(select(DMParticipant.user_id).where(DMParticipant.channel_id == channel.id))
        return set(result.scalars().all())

    if channel.server_id is None:
        return set()

    result = await db.execute(select(ServerMember.user_id).where(ServerMember.server_id == channel.server_id))
    return set(result.scalars().all())


@router.post("/channels/{channel_id}")
async def send_message(
    channel_id: int,
    payload: MessageCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    await rate_limiter.check(
        key_prefix="messages",
        actor_id=current_user.id,
        limit=settings.rate_limit_message_count,
        window_seconds=settings.rate_limit_message_window_seconds,
    )

    channel = await get_channel_or_404(db, channel_id)
    await ensure_channel_access(db, channel, current_user.id)

    if not payload.content and not payload.attachments:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Message cannot be empty")

    message = Message(
        channel_id=channel.id,
        author_id=current_user.id,
        content=payload.content,
        attachments=payload.attachments,
    )
    db.add(message)
    await db.commit()
    await db.refresh(message)

    serialized = MessageOut.model_validate(message).model_dump(mode="json")
    recipient_ids = await _channel_member_ids(db, channel)
    await publish_channel_event(
        channel.id,
        payload={
            "event": "message.created",
            "channel_id": channel.id,
            "data": serialized,
        },
        extra_user_ids=recipient_ids,
    )

    return success_response(serialized)


@router.get("/channels/{channel_id}")
async def get_message_history(
    channel_id: int,
    cursor: int | None = Query(default=None, description="Return messages with id < cursor"),
    limit: int = Query(default=50, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    channel = await get_channel_or_404(db, channel_id)
    await ensure_channel_access(db, channel, current_user.id)

    query = select(Message).where(Message.channel_id == channel_id)
    if cursor is not None:
        query = query.where(Message.id < cursor)

    query = query.order_by(desc(Message.id)).limit(limit + 1)
    result = await db.execute(query)
    rows = result.scalars().all()

    has_more = len(rows) > limit
    rows = rows[:limit]

    items = [MessageOut.model_validate(row).model_dump(mode="json") for row in rows]
    items.reverse()

    next_cursor = rows[-1].id if has_more and rows else None
    payload = MessageHistoryOut(items=[MessageOut(**item) for item in items], next_cursor=next_cursor)

    return success_response(payload.model_dump(mode="json"))


@router.patch("/{message_id}")
async def edit_message(
    message_id: int,
    payload: MessageUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    message = await get_message_or_404(db, message_id)
    channel = await get_channel_or_404(db, message.channel_id)
    await ensure_channel_access(db, channel, current_user.id)

    if message.author_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Can only edit your own messages")

    message.content = payload.content
    message.edited_at = datetime.now(tz=UTC)
    await db.commit()
    await db.refresh(message)

    serialized = MessageOut.model_validate(message).model_dump(mode="json")
    recipient_ids = await _channel_member_ids(db, channel)
    await publish_channel_event(
        message.channel_id,
        payload={
            "event": "message.updated",
            "channel_id": message.channel_id,
            "data": serialized,
        },
        extra_user_ids=recipient_ids,
    )

    return success_response(serialized)


@router.delete("/{message_id}")
async def delete_message(
    message_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    message = await get_message_or_404(db, message_id)
    channel = await get_channel_or_404(db, message.channel_id)
    await ensure_channel_access(db, channel, current_user.id)

    can_delete = message.author_id == current_user.id
    if not can_delete and channel.type != ChannelType.dm:
        membership = await _server_membership_for_channel(db, message, current_user.id)
        can_delete = membership is not None and _can_moderate(membership.role)

    if not can_delete:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cannot delete this message")

    await db.delete(message)
    await db.commit()

    recipient_ids = await _channel_member_ids(db, channel)
    await publish_channel_event(
        channel.id,
        payload={
            "event": "message.deleted",
            "channel_id": channel.id,
            "data": {"id": message_id},
        },
        extra_user_ids=recipient_ids,
    )

    return success_response({"deleted": True})


@router.put("/{message_id}/reactions")
async def add_reaction(
    message_id: int,
    payload: ReactionPayload,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    message = await get_message_or_404(db, message_id)
    channel = await get_channel_or_404(db, message.channel_id)
    await ensure_channel_access(db, channel, current_user.id)

    existing = await db.execute(
        select(Reaction).where(
            and_(
                Reaction.message_id == message.id,
                Reaction.user_id == current_user.id,
                Reaction.emoji == payload.emoji,
            )
        )
    )
    if existing.scalar_one_or_none() is None:
        db.add(Reaction(message_id=message.id, user_id=current_user.id, emoji=payload.emoji))
        await db.commit()

    event_data = {
        "message_id": message.id,
        "user_id": current_user.id,
        "emoji": payload.emoji,
    }
    recipient_ids = await _channel_member_ids(db, channel)
    await publish_channel_event(
        channel.id,
        payload={
            "event": "reaction.added",
            "channel_id": channel.id,
            "data": event_data,
        },
        extra_user_ids=recipient_ids,
    )

    return success_response(event_data)


@router.delete("/{message_id}/reactions")
async def remove_reaction(
    message_id: int,
    emoji: str = Query(..., min_length=1, max_length=64),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    message = await get_message_or_404(db, message_id)
    channel = await get_channel_or_404(db, message.channel_id)
    await ensure_channel_access(db, channel, current_user.id)

    result = await db.execute(
        select(Reaction).where(
            and_(
                Reaction.message_id == message.id,
                Reaction.user_id == current_user.id,
                Reaction.emoji == emoji,
            )
        )
    )
    reaction = result.scalar_one_or_none()
    if reaction is not None:
        await db.delete(reaction)
        await db.commit()

    event_data = {
        "message_id": message.id,
        "user_id": current_user.id,
        "emoji": emoji,
    }
    recipient_ids = await _channel_member_ids(db, channel)
    await publish_channel_event(
        channel.id,
        payload={
            "event": "reaction.removed",
            "channel_id": channel.id,
            "data": event_data,
        },
        extra_user_ids=recipient_ids,
    )

    return success_response(event_data)
