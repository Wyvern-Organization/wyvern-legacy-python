from datetime import UTC, date, datetime, time
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import String, and_, delete, desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.database import get_db
from app.models import Channel, ChannelType, DMHiddenState, DMParticipant, MemberRole, Message, MessageBookmark, Reaction, Server, ServerMember, User
from app.schemas.message import (
    BookmarkListOut,
    MessageBookmarkOut,
    MessageCreate,
    MessageHistoryOut,
    MessagePinListOut,
    MessageOut,
    MessageSearchHitOut,
    MessageReactionOut,
    MessageReplyPreviewOut,
    MessageUpdate,
    ReactionPayload,
)
from app.schemas.user import UserPublicOut
from app.services.access import ensure_channel_access, get_channel_or_404, get_message_or_404
from app.services.community import record_server_activity
from app.services.pubsub import publish_channel_event
from app.services.rate_limiter import rate_limiter
from app.services.sync_bridge import bump_sync_version, enqueue_delete_event, enqueue_upsert_event
from app.utils.dependencies import get_current_user
from app.utils.responses import success_response


router = APIRouter(prefix="/messages", tags=["messages"])
settings = get_settings()


def _can_moderate(role: MemberRole) -> bool:
    return role in {MemberRole.owner, MemberRole.admin, MemberRole.moderator}


async def _server_membership_for_channel(db: AsyncSession, message: Message, user_id: str) -> ServerMember | None:
    channel = await get_channel_or_404(db, message.channel_id)
    if channel.type == ChannelType.dm or channel.server_id is None:
        return None

    result = await db.execute(
        select(ServerMember).where(
            and_(ServerMember.server_id == channel.server_id, ServerMember.user_id == user_id)
        )
    )
    return result.scalar_one_or_none()


async def _channel_member_ids(db: AsyncSession, channel: Channel) -> set[str]:
    if channel.type == ChannelType.dm:
        result = await db.execute(select(DMParticipant.user_id).where(DMParticipant.channel_id == channel.id))
        return set(result.scalars().all())

    if channel.server_id is None:
        return set()

    result = await db.execute(select(ServerMember.user_id).where(ServerMember.server_id == channel.server_id))
    return set(result.scalars().all())


async def _serialize_dm_channel_for_ws(db: AsyncSession, channel: Channel) -> dict:
    result = await db.execute(
        select(User)
        .join(DMParticipant, DMParticipant.user_id == User.id)
        .where(DMParticipant.channel_id == channel.id)
        .order_by(User.id.asc())
    )
    return {
        "id": channel.id,
        "server_id": channel.server_id,
        "name": channel.name,
        "type": channel.type.value if hasattr(channel.type, "value") else str(channel.type),
        "position": channel.position,
        "category": channel.category,
        "created_by": channel.created_by,
        "created_at": channel.created_at.isoformat() if channel.created_at else None,
        "participants": [
            {
                "id": user.id,
                "username": user.username,
                "discriminator": user.discriminator,
                "display_name": user.display_name,
                "avatar": user.avatar,
            }
            for user in result.scalars().all()
        ],
    }


async def _can_pin_message(db: AsyncSession, message: Message, current_user_id: str, channel: Channel) -> bool:
    if message.author_id == current_user_id:
        return True
    if channel.type == ChannelType.dm:
        return True
    membership = await _server_membership_for_channel(db, message, current_user_id)
    return membership is not None and _can_moderate(membership.role)


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
    grouped: dict[str, list[str]] = defaultdict(list)
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


async def _bookmark_ids_for_user(db: AsyncSession, user_id: str, message_ids: list[str] | set[str]) -> set[str]:
    ids = [str(message_id) for message_id in message_ids if str(message_id)]
    if not ids:
        return set()
    result = await db.execute(
        select(MessageBookmark.message_id).where(
            MessageBookmark.user_id == user_id,
            MessageBookmark.message_id.in_(ids),
        )
    )
    return {str(message_id) for message_id in result.scalars().all()}


def _serialize_message(message: Message, *, bookmarked_by_me: bool = False) -> dict:
    payload = MessageOut(
        id=message.id,
        channel_id=message.channel_id,
        author_id=message.author_id,
        reply_to_id=message.reply_to_id,
        content=message.content,
        attachments=list(message.attachments or []),
        created_at=message.created_at,
        edited_at=message.edited_at,
        is_pinned=bool(getattr(message, "is_pinned", False)),
        webhook_name=getattr(message, "webhook_name", None),
        webhook_avatar=getattr(message, "webhook_avatar", None),
        bookmarked_by_me=bookmarked_by_me,
        reply_to=_serialize_reply_preview(message.reply_to),
        reactions=_serialize_reactions(message),
    )
    return payload.model_dump(mode="json")


def _serialize_reaction_event(message: Message, *, user_id: str, emoji: str) -> dict:
    return {
        "message_id": message.id,
        "channel_id": message.channel_id,
        "user_id": user_id,
        "emoji": emoji,
        "message": _serialize_message(message),
    }


async def _load_message_with_relations(db: AsyncSession, message_id: str) -> Message:
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


def _format_message_cursor(message: Message) -> str:
    return f"{message.created_at.astimezone(UTC).isoformat()}|{message.id}"


async def _parse_message_cursor(db: AsyncSession, cursor: str | None) -> tuple[datetime, str] | None:
    if not cursor:
        return None
    if "|" in cursor:
        raw_created_at, raw_id = cursor.split("|", 1)
        try:
            created_at = datetime.fromisoformat(raw_created_at.replace("Z", "+00:00"))
        except ValueError:
            return None
        return created_at, raw_id

    legacy_lookup = await db.execute(select(Message).where(Message.id == cursor))
    message = legacy_lookup.scalar_one_or_none()
    if message is None:
        return None
    return message.created_at, message.id


async def _resolve_channel_scope(
    db: AsyncSession,
    *,
    channel_id: str | None,
    server_id: str | None,
    current_user_id: str,
) -> tuple[list[str], str | None]:
    if channel_id is not None:
        channel = await get_channel_or_404(db, channel_id)
        await ensure_channel_access(db, channel, current_user_id)
        return [channel.id], channel.server_id

    if server_id is not None:
        membership = await db.execute(
            select(ServerMember).where(
                and_(ServerMember.server_id == server_id, ServerMember.user_id == current_user_id)
            )
        )
        if membership.scalar_one_or_none() is None:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not a server member")

        channel_ids_result = await db.execute(
            select(Channel.id).where(
                and_(Channel.server_id == server_id, Channel.type.in_([ChannelType.text, ChannelType.voice, ChannelType.dm]))
            )
        )
        return [str(item) for item in channel_ids_result.scalars().all()], server_id

    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Either channel_id or server_id is required")


@router.post("/channels/{channel_id}")
async def send_message(
    channel_id: str,
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

    if channel.type == ChannelType.dm:
        await db.execute(delete(DMHiddenState).where(DMHiddenState.channel_id == channel.id))

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
    serialized["author"] = UserPublicOut.model_validate(current_user).model_dump(mode="json")
    recipient_ids = await _channel_member_ids(db, channel)
    event_payload = {
        "event": "message.created",
        "channel_id": channel.id,
        "data": serialized,
    }
    if channel.type == ChannelType.dm:
        event_payload["channel"] = await _serialize_dm_channel_for_ws(db, channel)
    await publish_channel_event(
        channel.id,
        payload=event_payload,
        extra_user_ids=recipient_ids,
    )

    return success_response(serialized)


@router.get("/channels/{channel_id}")
async def get_message_history(
    channel_id: str,
    cursor: str | None = Query(default=None, description="Return messages before the encoded created_at/id cursor"),
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
    parsed_cursor = await _parse_message_cursor(db, cursor)
    if parsed_cursor is not None:
        cursor_created_at, cursor_id = parsed_cursor
        query = query.where(
            or_(
                Message.created_at < cursor_created_at,
                and_(Message.created_at == cursor_created_at, Message.id < cursor_id),
            )
        )

    query = query.order_by(desc(Message.created_at), desc(Message.id)).limit(limit + 1)
    result = await db.execute(query)
    rows = result.scalars().all()

    has_more = len(rows) > limit
    rows = rows[:limit]

    bookmark_ids = await _bookmark_ids_for_user(db, current_user.id, [row.id for row in rows])
    items = [_serialize_message(row, bookmarked_by_me=row.id in bookmark_ids) for row in rows]
    items.reverse()

    next_cursor = _format_message_cursor(rows[-1]) if has_more and rows else None
    payload = MessageHistoryOut(items=[MessageOut(**item) for item in items], next_cursor=next_cursor)

    return success_response(payload.model_dump(mode="json"))


@router.get("/search")
async def search_messages(
    q: str = Query(default="", max_length=200),
    channel_id: str | None = Query(default=None),
    server_id: str | None = Query(default=None),
    author_id: str | None = Query(default=None),
    before: str | None = Query(default=None),
    after: str | None = Query(default=None),
    has_attachments: bool | None = Query(default=None),
    has_reactions: bool | None = Query(default=None),
    is_pinned: bool | None = Query(default=None),
    created_before: date | None = Query(default=None),
    created_after: date | None = Query(default=None),
    limit: int = Query(default=25, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    channel_ids, _server_scope = await _resolve_channel_scope(
        db,
        channel_id=channel_id,
        server_id=server_id,
        current_user_id=current_user.id,
    )

    query = (
        select(Message)
        .join(Channel, Channel.id == Message.channel_id)
        .options(
            selectinload(Message.reply_to),
            selectinload(Message.reactions),
        )
        .where(Message.channel_id.in_(channel_ids))
    )

    if q.strip():
        term = f"%{q.strip()}%"
        query = query.join(User, User.id == Message.author_id).where(
            or_(
                Message.content.ilike(term),
                func.coalesce(func.cast(Message.attachments, String), "").ilike(term),
                func.coalesce(Message.webhook_name, "").ilike(term),
                func.coalesce(User.username, "").ilike(term),
                func.coalesce(User.display_name, "").ilike(term),
                func.coalesce(User.discriminator, "").ilike(term),
            )
        )

    if author_id is not None:
        query = query.where(Message.author_id == author_id)
    if before is not None:
        query = query.where(Message.id < before)
    if after is not None:
        query = query.where(Message.id > after)
    if has_attachments is not None:
        attachment_count = func.coalesce(func.jsonb_array_length(Message.attachments), 0)
        query = query.where(attachment_count > 0 if has_attachments else attachment_count == 0)
    if has_reactions is not None:
        reaction_exists = select(Reaction.message_id).where(Reaction.message_id == Message.id).exists()
        query = query.where(reaction_exists if has_reactions else ~reaction_exists)
    if is_pinned is not None:
        query = query.where(Message.is_pinned.is_(is_pinned))
    if created_before is not None:
        upper_bound = datetime.combine(created_before, time.max, tzinfo=UTC)
        query = query.where(Message.created_at <= upper_bound)
    if created_after is not None:
        lower_bound = datetime.combine(created_after, time.min, tzinfo=UTC)
        query = query.where(Message.created_at >= lower_bound)

    rows = (await db.execute(query.order_by(desc(Message.created_at), desc(Message.id)).limit(limit))).scalars().all()
    message_ids = [row.id for row in rows]
    bookmark_ids = await _bookmark_ids_for_user(db, current_user.id, message_ids)

    channel_lookup = {}
    if channel_ids:
        channel_result = await db.execute(
            select(
                Channel.id,
                Channel.name,
                Channel.type,
                Channel.server_id,
                Server.name.label("server_name"),
            )
            .outerjoin(Server, Server.id == Channel.server_id)
            .where(Channel.id.in_(channel_ids))
        )
        for row in channel_result.all():
            channel_lookup[str(row.id)] = {
                "name": row.name,
                "type": row.type.value if hasattr(row.type, "value") else str(row.type),
                "server_id": str(row.server_id) if row.server_id is not None else None,
                "server_name": getattr(row, "server_name", None),
            }

    items = []
    for row in rows:
        channel_info = channel_lookup.get(str(row.channel_id), {})
        items.append(
            MessageSearchHitOut(
                message=MessageOut(**_serialize_message(row, bookmarked_by_me=row.id in bookmark_ids)),
                channel_id=str(row.channel_id),
                channel_name=channel_info.get("name") or "channel",
                channel_type=channel_info.get("type") or "text",
                server_id=channel_info.get("server_id"),
                server_name=channel_info.get("server_name"),
            )
        )

    return success_response([item.model_dump(mode="json") for item in items])


@router.get("/pins/channels/{channel_id}")
async def list_pinned_messages(
    channel_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    channel = await get_channel_or_404(db, channel_id)
    await ensure_channel_access(db, channel, current_user.id)

    result = await db.execute(
        select(Message)
        .where(and_(Message.channel_id == channel_id, Message.is_pinned.is_(True)))
        .options(
            selectinload(Message.reply_to),
            selectinload(Message.reactions),
        )
        .order_by(desc(Message.created_at), desc(Message.id))
    )
    rows = result.scalars().all()
    bookmark_ids = await _bookmark_ids_for_user(db, current_user.id, [row.id for row in rows])
    payload = MessagePinListOut(items=[MessageOut(**_serialize_message(row, bookmarked_by_me=row.id in bookmark_ids)) for row in rows])
    return success_response(payload.model_dump(mode="json"))


@router.put("/{message_id}/pin")
async def pin_message(
    message_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    message = await get_message_or_404(db, message_id)
    channel = await get_channel_or_404(db, message.channel_id)
    await ensure_channel_access(db, channel, current_user.id)
    if not await _can_pin_message(db, message, current_user.id, channel):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cannot pin this message")

    message.is_pinned = True
    base_sync_version = bump_sync_version(message)
    await enqueue_upsert_event(db, "message", message, base_sync_version=base_sync_version)
    await record_server_activity(
        db,
        server_id=channel.server_id,
        actor_user_id=current_user.id,
        action="message.pinned",
        target_type="message",
        target_id=str(message.id),
        metadata={"channel_id": channel.id},
    )
    await db.commit()
    message = await _load_message_with_relations(db, message.id)
    recipient_ids = await _channel_member_ids(db, channel)
    await publish_channel_event(
        channel.id,
        payload={"event": "message.updated", "channel_id": channel.id, "data": _serialize_message(message)},
        extra_user_ids=recipient_ids,
    )
    return success_response(_serialize_message(message))


@router.delete("/{message_id}/pin")
async def unpin_message(
    message_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    message = await get_message_or_404(db, message_id)
    channel = await get_channel_or_404(db, message.channel_id)
    await ensure_channel_access(db, channel, current_user.id)
    if not await _can_pin_message(db, message, current_user.id, channel):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cannot unpin this message")

    message.is_pinned = False
    base_sync_version = bump_sync_version(message)
    await enqueue_upsert_event(db, "message", message, base_sync_version=base_sync_version)
    await record_server_activity(
        db,
        server_id=channel.server_id,
        actor_user_id=current_user.id,
        action="message.unpinned",
        target_type="message",
        target_id=str(message.id),
        metadata={"channel_id": channel.id},
    )
    await db.commit()
    message = await _load_message_with_relations(db, message.id)
    recipient_ids = await _channel_member_ids(db, channel)
    await publish_channel_event(
        channel.id,
        payload={"event": "message.updated", "channel_id": channel.id, "data": _serialize_message(message)},
        extra_user_ids=recipient_ids,
    )
    return success_response(_serialize_message(message))


@router.get("/bookmarks")
async def list_bookmarks(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    result = await db.execute(
        select(MessageBookmark)
        .where(MessageBookmark.user_id == current_user.id)
        .options(
            selectinload(MessageBookmark.message).selectinload(Message.reply_to),
            selectinload(MessageBookmark.message).selectinload(Message.reactions),
        )
        .order_by(desc(MessageBookmark.created_at), desc(MessageBookmark.id))
    )
    bookmarks = result.scalars().all()
    message_ids = [bookmark.message_id for bookmark in bookmarks]
    bookmark_ids = await _bookmark_ids_for_user(db, current_user.id, message_ids)
    items = [
        MessageBookmarkOut(
            saved_at=bookmark.created_at,
            message=MessageOut(**_serialize_message(bookmark.message, bookmarked_by_me=bookmark.message_id in bookmark_ids)),
        )
        for bookmark in bookmarks
        if bookmark.message is not None
    ]
    return success_response(BookmarkListOut(items=items).model_dump(mode="json"))


@router.put("/{message_id}/bookmark")
async def bookmark_message(
    message_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    message = await get_message_or_404(db, message_id)
    channel = await get_channel_or_404(db, message.channel_id)
    await ensure_channel_access(db, channel, current_user.id)

    existing = await db.execute(
        select(MessageBookmark).where(
            and_(MessageBookmark.user_id == current_user.id, MessageBookmark.message_id == message.id)
        )
    )
    bookmark = existing.scalar_one_or_none()
    bookmarked = True
    if bookmark is None:
        bookmark = MessageBookmark(user_id=current_user.id, message_id=message.id)
        db.add(bookmark)
        await db.flush()
        await record_server_activity(
            db,
            server_id=channel.server_id,
            actor_user_id=current_user.id,
            action="message.bookmarked",
            target_type="message",
            target_id=str(message.id),
            metadata={"channel_id": channel.id},
        )
    await db.commit()
    message = await _load_message_with_relations(db, message.id)
    return success_response({
        "bookmarked": bookmarked,
        "message": _serialize_message(message, bookmarked_by_me=True),
    })


@router.delete("/{message_id}/bookmark")
async def unbookmark_message(
    message_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    message = await get_message_or_404(db, message_id)
    channel = await get_channel_or_404(db, message.channel_id)
    await ensure_channel_access(db, channel, current_user.id)

    result = await db.execute(
        select(MessageBookmark).where(
            and_(MessageBookmark.user_id == current_user.id, MessageBookmark.message_id == message.id)
        )
    )
    bookmark = result.scalar_one_or_none()
    if bookmark is not None:
        await db.delete(bookmark)
        await record_server_activity(
            db,
            server_id=channel.server_id,
            actor_user_id=current_user.id,
            action="message.unbookmarked",
            target_type="message",
            target_id=str(message.id),
            metadata={"channel_id": channel.id},
        )
    await db.commit()
    message = await _load_message_with_relations(db, message.id)
    return success_response({
        "bookmarked": False,
        "message": _serialize_message(message, bookmarked_by_me=False),
    })


@router.patch("/{message_id}")
async def edit_message(
    message_id: str,
    payload: MessageUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    message = await get_message_or_404(db, message_id)
    channel = await get_channel_or_404(db, message.channel_id)
    await ensure_channel_access(db, channel, current_user.id)

    if message.author_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Can only edit your own messages")

    if not payload.content and not message.attachments:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Message cannot be empty")

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
    message_id: str,
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
    message_id: str,
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
    message_id: str,
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
