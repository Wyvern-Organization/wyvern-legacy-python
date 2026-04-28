from __future__ import annotations

from datetime import UTC, datetime
from hmac import compare_digest
import logging
from uuid import uuid4
from urllib.parse import urljoin

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import and_, desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models import Channel, ChannelType, DMParticipant, MemberRole, Message, Server, ServerMember, ServerWebhook, User, WebhookDeliveryLog
from app.schemas.community import WebhookCreateOut, WebhookCreateRequest, WebhookDeliveryOut, WebhookMessageRequest, WebhookOut
from app.schemas.message import MessageOut
from app.services.access import ensure_channel_access, get_channel_or_404
from app.services.community import generate_secret_token, hash_secret_token, record_server_activity
from app.services.pubsub import publish_channel_event
from app.utils.dependencies import get_current_user
from app.utils.responses import success_response


router = APIRouter(tags=["webhooks"])
logger = logging.getLogger(__name__)


def _can_manage_webhooks(role: MemberRole) -> bool:
    return role in {MemberRole.owner, MemberRole.admin}


async def _server_membership(db: AsyncSession, server_id: str, user_id: str) -> ServerMember | None:
    result = await db.execute(
        select(ServerMember).where(and_(ServerMember.server_id == server_id, ServerMember.user_id == user_id))
    )
    return result.scalar_one_or_none()


def _webhook_url(request: Request, webhook_id: str, token: str) -> str:
    base = str(request.base_url)
    return urljoin(base, f"api/v1/webhooks/{webhook_id}/{token}")


def _serialize_webhook(webhook: ServerWebhook, request: Request | None = None, token: str | None = None) -> dict:
    payload = WebhookOut(
        id=webhook.id,
        server_id=webhook.server_id,
        channel_id=webhook.channel_id,
        name=webhook.name,
        description=webhook.description,
        active=webhook.active,
        created_by=webhook.created_by,
        created_at=webhook.created_at,
        updated_at=webhook.updated_at,
        last_used_at=webhook.last_used_at,
        webhook_url=_webhook_url(request, webhook.id, token) if request and token else None,
    )
    return payload.model_dump(mode="json")


async def _load_webhook_or_404(db: AsyncSession, webhook_id: str) -> ServerWebhook:
    result = await db.execute(select(ServerWebhook).where(ServerWebhook.id == webhook_id))
    webhook = result.scalar_one_or_none()
    if webhook is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Webhook not found")
    return webhook


async def _load_webhook_by_token(db: AsyncSession, webhook_id: str, token: str) -> ServerWebhook:
    webhook = await _load_webhook_or_404(db, webhook_id)
    if not webhook.active or not compare_digest(webhook.token_hash, hash_secret_token(token)):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Webhook not found")
    return webhook


async def _channel_member_ids(db: AsyncSession, channel: Channel) -> set[str]:
    if channel.type == ChannelType.dm:
        result = await db.execute(select(DMParticipant.user_id).where(DMParticipant.channel_id == channel.id))
        return {str(user_id) for user_id in result.scalars().all()}
    if channel.server_id is None:
        return set()
    result = await db.execute(select(ServerMember.user_id).where(ServerMember.server_id == channel.server_id))
    return {str(user_id) for user_id in result.scalars().all()}


@router.get("/servers/{server_id}/webhooks")
async def list_webhooks(
    server_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    membership = await _server_membership(db, server_id, current_user.id)
    if membership is None or not _can_manage_webhooks(membership.role):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")

    result = await db.execute(
        select(ServerWebhook)
        .where(ServerWebhook.server_id == server_id)
        .order_by(desc(ServerWebhook.created_at), desc(ServerWebhook.id))
    )
    items = [_serialize_webhook(webhook) for webhook in result.scalars().all()]
    return success_response({"items": items})


@router.post("/servers/{server_id}/webhooks")
async def create_webhook(
    server_id: str,
    payload: WebhookCreateRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    membership = await _server_membership(db, server_id, current_user.id)
    if membership is None or not _can_manage_webhooks(membership.role):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")

    channel = await get_channel_or_404(db, payload.channel_id)
    if channel.server_id != server_id or channel.type != ChannelType.text:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Webhook channel must be a text channel in this server")
    await ensure_channel_access(db, channel, current_user.id)

    token = generate_secret_token()
    webhook = ServerWebhook(
        server_id=server_id,
        channel_id=channel.id,
        name=payload.name,
        description=payload.description,
        token_hash=hash_secret_token(token),
        created_by=current_user.id,
        active=True,
        created_at=datetime.now(tz=UTC),
        updated_at=datetime.now(tz=UTC),
    )
    db.add(webhook)
    await db.flush()
    await record_server_activity(
        db,
        server_id=server_id,
        actor_user_id=current_user.id,
        action="webhook.created",
        target_type="webhook",
        target_id=str(webhook.id),
        metadata={"channel_id": channel.id, "name": webhook.name},
    )
    await db.commit()
    await db.refresh(webhook)
    return success_response(
        WebhookCreateOut(
            webhook=WebhookOut.model_validate(webhook).model_copy(update={"webhook_url": _webhook_url(request, webhook.id, token)}),
            token=token,
            webhook_url=_webhook_url(request, webhook.id, token),
        ).model_dump(mode="json")
    )


@router.delete("/webhooks/{webhook_id}")
async def delete_webhook(
    webhook_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    webhook = await _load_webhook_or_404(db, webhook_id)
    membership = await _server_membership(db, webhook.server_id, current_user.id)
    if membership is None or not _can_manage_webhooks(membership.role):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")

    webhook.active = False
    webhook.updated_at = datetime.now(tz=UTC)
    await record_server_activity(
        db,
        server_id=webhook.server_id,
        actor_user_id=current_user.id,
        action="webhook.deleted",
        target_type="webhook",
        target_id=str(webhook.id),
        metadata={"channel_id": webhook.channel_id, "name": webhook.name},
    )
    await db.commit()
    return success_response({"deleted": True})


@router.get("/servers/{server_id}/webhooks/{webhook_id}/deliveries")
async def list_webhook_deliveries(
    server_id: str,
    webhook_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    webhook = await _load_webhook_or_404(db, webhook_id)
    if webhook.server_id != server_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Webhook not found")
    membership = await _server_membership(db, server_id, current_user.id)
    if membership is None or not _can_manage_webhooks(membership.role):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")

    result = await db.execute(
        select(WebhookDeliveryLog)
        .where(WebhookDeliveryLog.webhook_id == webhook_id)
        .order_by(desc(WebhookDeliveryLog.created_at), desc(WebhookDeliveryLog.id))
        .limit(50)
    )
    items = [
        WebhookDeliveryOut.model_validate(item).model_dump(mode="json")
        for item in result.scalars().all()
    ]
    return success_response({"items": items})


@router.post("/webhooks/{webhook_id}/{token}")
async def invoke_webhook(
    webhook_id: str,
    token: str,
    payload: WebhookMessageRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    webhook = await _load_webhook_by_token(db, webhook_id, token)
    channel = await get_channel_or_404(db, webhook.channel_id)
    if channel.type != ChannelType.text:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Webhook channel is not available")

    attempts = 0
    last_error = None
    message = None
    delivery_request_id = str(uuid4())
    for attempt in range(3):
        attempts = attempt + 1
        try:
            if not payload.content and not payload.attachments:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Webhook message cannot be empty")
            message = Message(
                channel_id=channel.id,
                author_id=webhook.created_by,
                content=payload.content,
                attachments=payload.attachments,
                webhook_name=(payload.username or webhook.name),
                webhook_avatar=payload.avatar_url,
            )
            db.add(message)
            await db.flush()
            webhook.last_used_at = datetime.now(tz=UTC)
            webhook.updated_at = webhook.last_used_at
            await record_server_activity(
                db,
                server_id=webhook.server_id,
                actor_user_id=webhook.created_by,
                action="webhook.posted",
                target_type="message",
                target_id=str(message.id),
                metadata={"channel_id": channel.id, "webhook_id": webhook.id},
            )
            delivery = WebhookDeliveryLog(
                webhook_id=webhook.id,
                request_id=delivery_request_id,
                status="applied",
                attempts=attempts,
                payload=payload.model_dump(mode="json"),
                response_message="Message created",
            )
            db.add(delivery)
            await db.commit()
            break
        except Exception as exc:
            last_error = str(exc)
            await db.rollback()
            logger.warning("Webhook delivery attempt %s failed for webhook %s: %s", attempts, webhook.id, exc)
            if attempt >= 2:
                delivery = WebhookDeliveryLog(
                    webhook_id=webhook.id,
                    request_id=delivery_request_id,
                    status="failed",
                    attempts=attempts,
                    payload=payload.model_dump(mode="json"),
                    response_message="Webhook delivery failed",
                )
                db.add(delivery)
                await db.commit()
                raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Webhook delivery failed") from exc

    if message is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Webhook delivery failed")

    await db.refresh(message)
    serialized = MessageOut.model_validate(message).model_dump(mode="json")
    recipient_ids = await _channel_member_ids(db, channel)
    await publish_channel_event(
        channel.id,
        payload={"event": "message.created", "channel_id": channel.id, "data": serialized},
        extra_user_ids=recipient_ids,
    )
    return success_response({"message": serialized})
