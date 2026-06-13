from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import ApiToken, User
from app.services.admin_allowlist import admin_allowlist_service
from app.services.api_tokens import create_api_token, serialize_api_token
from app.services.mcp_server import serialize_mcp_connector
from app.utils.dependencies import get_current_active_user


router = APIRouter(prefix="/ai", tags=["ai"])


async def require_admin(current_user: User = Depends(get_current_active_user)) -> User:
    if not await admin_allowlist_service.is_admin(current_user.username, current_user.discriminator):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return current_user


def _normalize_token_name(value: object, fallback: str = "Wyvern OpenAI API Token") -> str:
    name = str(value or "").strip()
    if not name:
        name = fallback
    return name[:120]


async def _load_tokens(db: AsyncSession, user_id: str) -> list[ApiToken]:
    result = await db.execute(select(ApiToken).where(ApiToken.user_id == user_id).order_by(desc(ApiToken.created_at), desc(ApiToken.id)))
    return list(result.scalars().all())


def _base_url_from_request(request: Request) -> str:
    return str(request.base_url).rstrip("/")


@router.get("/api-tokens")
async def list_api_tokens(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
) -> dict[str, Any]:
    result = await db.execute(
        select(ApiToken)
        .where(ApiToken.user_id == current_user.id)
        .order_by(desc(ApiToken.created_at), desc(ApiToken.id))
    )
    tokens = result.scalars().all()
    return {
        "tokens": [serialize_api_token(token) for token in tokens],
    }


@router.get("/mcp-connection")
async def get_mcp_connection(
    request: Request,
    current_user: User = Depends(get_current_active_user),
) -> dict[str, Any]:
    _ = current_user
    return {"connected": True, "connection": serialize_mcp_connector(_base_url_from_request(request))}


@router.post("/mcp-connection")
async def create_mcp_connection(
    request: Request,
    current_user: User = Depends(get_current_active_user),
) -> dict[str, Any]:
    _ = current_user
    return {"connected": True, "connection": serialize_mcp_connector(_base_url_from_request(request))}


@router.delete("/mcp-connection")
async def revoke_mcp_connection(
    current_user: User = Depends(get_current_active_user),
) -> dict[str, Any]:
    _ = current_user
    return {"connected": True, "connection": None, "revoked_count": 0}


@router.post("/api-tokens")
async def create_api_tokens(
    payload: dict[str, Any],
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
) -> dict[str, Any]:
    token, secret = await create_api_token(db, current_user, _normalize_token_name(payload.get("name")))
    await db.commit()
    await db.refresh(token)
    return {
        "token": serialize_api_token(token, secret=secret),
    }


@router.delete("/api-tokens/{token_id}")
async def revoke_api_token(
    token_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
) -> dict[str, Any]:
    result = await db.execute(select(ApiToken).where(ApiToken.id == token_id, ApiToken.user_id == current_user.id))
    token = result.scalar_one_or_none()
    if token is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API token not found")

    if token.revoked_at is None:
        token.revoked_at = datetime.now(tz=UTC)
    await db.commit()
    return {
        "token": serialize_api_token(token),
    }


@router.post("/api-tokens/{token_id}/rotate")
async def rotate_api_token(
    token_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
) -> dict[str, Any]:
    result = await db.execute(select(ApiToken).where(ApiToken.id == token_id, ApiToken.user_id == current_user.id))
    token = result.scalar_one_or_none()
    if token is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API token not found")

    if token.revoked_at is None:
        token.revoked_at = datetime.now(tz=UTC)

    replacement, secret = await create_api_token(db, current_user, token.name)
    await db.commit()
    await db.refresh(replacement)
    return {
        "revoked_token": serialize_api_token(token),
        "token": serialize_api_token(replacement, secret=secret),
    }


@router.post("/api-tokens/revoke-all")
async def revoke_all_api_tokens(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
) -> dict[str, Any]:
    tokens = await _load_tokens(db, current_user.id)
    now = datetime.now(tz=UTC)
    revoked = 0
    for token in tokens:
        if token.revoked_at is None:
            token.revoked_at = now
            revoked += 1
    await db.commit()
    return {
        "revoked_count": revoked,
        "tokens": [serialize_api_token(token) for token in tokens],
    }


@router.post("/api-tokens/rotate-all")
async def rotate_all_api_tokens(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
) -> dict[str, Any]:
    tokens = await _load_tokens(db, current_user.id)
    now = datetime.now(tz=UTC)
    replacements: list[dict[str, Any]] = []
    revoked_tokens: list[dict[str, Any]] = []
    for token in tokens:
        if token.revoked_at is None:
            token.revoked_at = now
        replacement, secret = await create_api_token(db, current_user, token.name)
        replacements.append(serialize_api_token(replacement, secret=secret))
        revoked_tokens.append(serialize_api_token(token))
    await db.commit()
    return {
        "revoked_tokens": revoked_tokens,
        "tokens": replacements,
    }
