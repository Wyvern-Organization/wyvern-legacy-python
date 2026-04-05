from datetime import UTC, datetime
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.database import get_db
from app.models import Channel, ChannelType, DMParticipant, MemberRole, Message, Reaction, ServerMember, User
from app.schemas.message import (
    MessageCreate,
    MessageHistoryOut,
    MessageOut,
    MessageReactionOut,
    MessageReplyPreviewOut,
    MessageUpdate,
    ReactionPayload,
)
from app.services.access import ensure_channel_access, get_channel_or_404, get_message_or_404
from app.services.pubsub import publish_channel_event
from app.services.rate_limiter import rate_limiter
from app.services.sync_bridge import bump_sync_version, enqueue_delete_event, enqueue_upsert_event
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


def _serialize_reply_preview(message: Message | None) -> MessageReplyPreviewOut | None:
    if message is None:
        return None

    return MessageReplyPreviewOut(
        id=message.id,
        author_id=message.author_id,
        content=message.content,
        attachments=list(message.attachments or []),
        created_at=message.created_at,
        edited_at=message.edited_at,
    )


def _serialize_reactions(message: Message) -> list[MessageReactionOut]:
    grouped: dict[str, list[int]] = defaultdict(list)
    for reaction in message.reactions or []:
        grouped[reaction.emoji].append(reaction.user_id)

    reaction_items = [
        MessageReactionOut(
            emoji=emoji,
            count=len(user_ids),
            users=sorted(set(user_ids)),
        )
        for emoji, user_ids in grouped.items()
    ]
    return sorted(reaction_items, key=lambda item: (-item.count, item.emoji))


def _serialize_message(message: Message) -> dict:
    payload = MessageOut(
        id=message.id,
        channel_id=message.channel_id,
        author_id=message.author_id,
        reply_to_id=message.reply_to_id,
        content=message.content,
        attachments=list(message.attachments or []),
        created_at=message.created_at,
        edited_at=message.edited_at,
        reply_to=_serialize_reply_preview(message.reply_to),
        reactions=_serialize_reactions(message),
    )
    return payload.model_dump(mode="json")


def _serialize_reaction_event(message: Message, *, user_id: int, emoji: str) -> dict:
    return {
        "message_id": message.id,
        "channel_id": message.channel_id,
        "user_id": user_id,
        "emoji": emoji,
        "message": _serialize_message(message),
    }


async def _load_message_with_relations(db: AsyncSession, message_id: int) -> Message:
    result = await db.execute(
        select(Message)
        .where(Message.id == message_id)
        .options(
            selectinload(Message.reply_to),
            selectinload(Message.reactions),
        )
    )
    message = result.scalar_one_or_none()
    if message is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Message not found")
    return message


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

    reply_to_id = payload.reply_to_id
    if reply_to_id is not None:
        reply_message = await get_message_or_404(db, reply_to_id)
        if reply_message.channel_id != channel.id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Replies must target a message in the same channel",
            )

    message = Message(
        channel_id=channel.id,
        author_id=current_user.id,
        reply_to_id=reply_to_id,
        content=payload.content,
        attachments=payload.attachments,
    )
    db.add(message)
    await db.flush()
    await enqueue_upsert_event(db, "message", message, base_sync_version=0)
    await db.commit()
    message = await _load_message_with_relations(db, message.id)

    serialized = _serialize_message(message)
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

    query = (
        select(Message)
        .where(Message.channel_id == channel_id)
        .options(
            selectinload(Message.reply_to),
            selectinload(Message.reactions),
        )
    )
    if cursor is not None:
        query = query.where(Message.id < cursor)

    query = query.order_by(desc(Message.id)).limit(limit + 1)
    result = await db.execute(query)
    rows = result.scalars().all()

    has_more = len(rows) > limit
    rows = rows[:limit]

    items = [_serialize_message(row) for row in rows]
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
    base_sync_version = bump_sync_version(message)
    await enqueue_upsert_event(db, "message", message, base_sync_version=base_sync_version)
    await db.commit()
    message = await _load_message_with_relations(db, message.id)

    serialized = _serialize_message(message)
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

    await enqueue_delete_event(db, "message", message, base_sync_version=message.sync_version)
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
        reaction = Reaction(message_id=message.id, user_id=current_user.id, emoji=payload.emoji)
        db.add(reaction)
        await db.flush()
        await enqueue_upsert_event(db, "reaction", reaction, base_sync_version=0)
        await db.commit()

    message = await _load_message_with_relations(db, message.id)
    event_data = _serialize_reaction_event(message, user_id=current_user.id, emoji=payload.emoji)
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
        await enqueue_delete_event(db, "reaction", reaction, base_sync_version=reaction.sync_version)
        await db.delete(reaction)
        await db.commit()

    message = await _load_message_with_relations(db, message.id)
    event_data = _serialize_reaction_event(message, user_id=current_user.id, emoji=emoji)
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
