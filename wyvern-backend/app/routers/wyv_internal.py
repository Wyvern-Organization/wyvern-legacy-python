from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models import User
from app.schemas.wyv import WyvApiTokenIntrospectRequest, WyvApiTokenIntrospectionOut, WyvBridgeGrantRequest
from app.services.api_tokens import get_active_api_token_by_secret
from app.services.wyv_bridge import (
    consume_wyv_handoff_grant,
    decode_wyv_handoff_grant,
    serialize_wyv_user,
    verify_wyv_bridge_signature,
    wyv_bridge_is_configured,
)
from app.utils.responses import success_response


router = APIRouter(prefix="/internal/wyv", tags=["wyv"])


def _verify_bridge_request(request: Request, body: bytes) -> None:
    if not wyv_bridge_is_configured():
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Wyv bridge is not configured")
    try:
        verify_wyv_bridge_signature(
            body,
            request.headers.get("X-Wyv-Bridge-Timestamp"),
            request.headers.get("X-Wyv-Bridge-Signature"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc


@router.post("/session-exchange")
async def session_exchange(request: Request) -> dict:
    raw_body = await request.body()
    _verify_bridge_request(request, raw_body)
    payload = WyvBridgeGrantRequest.model_validate_json(raw_body)
    try:
        grant_payload = decode_wyv_handoff_grant(payload.grant)
        await consume_wyv_handoff_grant(grant_payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    user_id = str(grant_payload.get("sub") or "").strip()
    user_snapshot = grant_payload.get("user") if isinstance(grant_payload.get("user"), dict) else {}
    if not user_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Wyv handoff grant is missing a user id")

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()

    if user is None:
        if user_snapshot:
            return success_response({"user": user_snapshot})
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Wyvern user not found")

    return success_response({"user": serialize_wyv_user(user)})


@router.post("/api-token-introspect")
async def api_token_introspect(request: Request) -> dict:
    raw_body = await request.body()
    _verify_bridge_request(request, raw_body)
    payload = WyvApiTokenIntrospectRequest.model_validate_json(raw_body)

    async with AsyncSessionLocal() as db:
        token = await get_active_api_token_by_secret(db, payload.token)
        if token is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API token")

        result = await db.execute(select(User).where(User.id == token.user_id))
        user = result.scalar_one_or_none()
        if user is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="API token user not found")

        await db.commit()

    response = WyvApiTokenIntrospectionOut(
        token_id=str(token.id),
        token_name=str(token.name),
        user=serialize_wyv_user(user),
    )
    return success_response(response.model_dump(mode="json"))
