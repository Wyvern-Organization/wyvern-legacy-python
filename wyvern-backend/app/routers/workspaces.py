from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models import Channel, ChannelType, User, WorkspaceDocument, WorkspaceRevision
from app.schemas.community import WorkspaceDocumentOut, WorkspaceRevisionOut, WorkspaceUpdateRequest
from app.services.access import ensure_channel_access, get_channel_or_404
from app.services.community import record_server_activity
from app.services.pubsub import publish_channel_event
from app.utils.dependencies import get_current_active_user
from app.utils.responses import success_response


router = APIRouter(tags=["workspaces"])


def _normalize_mode(value: str | None) -> str:
    normalized = (value or "writing").strip().lower()
    return normalized if normalized in {"code", "writing"} else "writing"


def _normalize_visibility(value: str | None) -> str:
    normalized = (value or "public").strip().lower()
    return normalized if normalized in {"public", "private"} else "public"


def _normalize_language(value: str | None) -> str:
    normalized = (value or "plaintext").strip().lower()
    allowed = {"plaintext", "python", "javascript", "typescript", "html", "css", "json", "sql", "markdown"}
    return normalized if normalized in allowed else "plaintext"


async def _load_workspace(
    db: AsyncSession,
    channel_id: str,
    current_user_id: str,
    visibility: str = "public",
) -> WorkspaceDocument | None:
    normalized_visibility = _normalize_visibility(visibility)
    query = (
        select(WorkspaceDocument)
        .where(WorkspaceDocument.channel_id == channel_id)
        .options(selectinload(WorkspaceDocument.revisions))
    )
    if normalized_visibility == "private":
        query = query.where(
            WorkspaceDocument.visibility == "private",
            WorkspaceDocument.owner_user_id == current_user_id,
        )
    else:
        query = query.where(
            WorkspaceDocument.visibility == "public",
            WorkspaceDocument.owner_user_id.is_(None),
        )
    result = await db.execute(query)
    return result.scalar_one_or_none()


def _default_workspace_title(channel: Channel) -> str:
    base = channel.name or "Channel"
    return f"{base} Workspace"


def _serialize_workspace(document: WorkspaceDocument, revisions: list[WorkspaceRevision] | None = None) -> WorkspaceDocumentOut:
    revision_rows = revisions if revisions is not None else list(document.revisions or [])
    ordered = sorted(revision_rows, key=lambda row: row.created_at, reverse=True)
    payload = WorkspaceDocumentOut(
        id=document.id,
        channel_id=document.channel_id,
        title=document.title,
        mode=document.mode,
        language=_normalize_language(document.language),
        visibility=document.visibility,
        content=document.content,
        owner_user_id=document.owner_user_id,
        updated_by_user_id=document.updated_by_user_id,
        created_at=document.created_at,
        updated_at=document.updated_at,
        revisions=[
            WorkspaceRevisionOut.model_validate(row)
            for row in ordered[:20]
        ],
    )
    return payload


def _build_workspace_placeholder(channel: Channel, current_user_id: str, visibility: str = "public") -> WorkspaceDocumentOut:
    normalized_visibility = _normalize_visibility(visibility)
    now = datetime.now(tz=UTC)
    return WorkspaceDocumentOut(
        id=None,
        channel_id=channel.id,
        title=_default_workspace_title(channel),
        mode="writing",
        language="plaintext",
        visibility=normalized_visibility,
        content="",
        owner_user_id=current_user_id if normalized_visibility == "private" else None,
        updated_by_user_id=current_user_id,
        created_at=now,
        updated_at=now,
        revisions=[],
    )


@router.get("/channels/{channel_id}/workspace")
async def get_workspace(
    channel_id: str,
    visibility: str = Query(default="public"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> dict:
    channel = await get_channel_or_404(db, channel_id)
    await ensure_channel_access(db, channel, current_user.id)
    if channel.type == ChannelType.voice:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Voice channels do not have workspaces")

    normalized_visibility = _normalize_visibility(visibility)
    document = await _load_workspace(db, channel_id, current_user.id, normalized_visibility)
    if document is None:
        return success_response(_build_workspace_placeholder(channel, current_user.id, normalized_visibility).model_dump(mode="json"))

    payload = _serialize_workspace(document)
    return success_response(payload.model_dump(mode="json"))


@router.patch("/channels/{channel_id}/workspace")
async def update_workspace(
    channel_id: str,
    payload: WorkspaceUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> dict:
    channel = await get_channel_or_404(db, channel_id)
    await ensure_channel_access(db, channel, current_user.id)
    if channel.type == ChannelType.voice:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Voice channels do not have workspaces")

    next_visibility = _normalize_visibility(payload.visibility)
    document = await _load_workspace(db, channel_id, current_user.id, next_visibility)
    if document is None:
        document = WorkspaceDocument(
            channel_id=channel_id,
            title=_default_workspace_title(channel),
            mode="writing",
            language="plaintext",
            visibility=next_visibility,
            content="",
            owner_user_id=current_user.id if next_visibility == "private" else None,
            updated_by_user_id=current_user.id,
            created_at=datetime.now(tz=UTC),
            updated_at=datetime.now(tz=UTC),
        )
        db.add(document)
        await db.flush()

    next_mode = _normalize_mode(payload.mode)
    next_language = _normalize_language(payload.language or document.language)
    title = (payload.title or document.title or _default_workspace_title(channel)).strip()
    content = payload.content or ""

    changed = content != (document.content or "")
    document.title = title or document.title
    document.mode = next_mode
    document.language = next_language
    document.visibility = next_visibility
    document.content = content
    document.owner_user_id = current_user.id if next_visibility == "private" else None
    document.updated_by_user_id = current_user.id
    document.updated_at = datetime.now(tz=UTC)

    if changed:
        db.add(
            WorkspaceRevision(
                document_id=document.id,
                editor_user_id=current_user.id,
                content=content,
            )
        )

    if document.visibility == "public" and payload.log_activity:
        await record_server_activity(
            db,
            server_id=channel.server_id,
            actor_user_id=current_user.id,
            action="workspace.updated",
            target_type="workspace",
            target_id=str(document.channel_id),
            metadata={"channel_id": channel.id, "mode": document.mode, "language": document.language, "title": document.title, "visibility": document.visibility},
        )
    await db.commit()
    document = await _load_workspace(db, channel_id, current_user.id, next_visibility)
    if document is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Workspace unavailable")

    payload_out = _serialize_workspace(document)
    if document.visibility == "public":
        await publish_channel_event(
            channel.id,
            payload={"event": "workspace.updated", "channel_id": channel.id, "data": payload_out.model_dump(mode="json")},
        )
    return success_response(payload_out.model_dump(mode="json"))
