from datetime import UTC, datetime
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.models import RefreshToken, User
from app.schemas.auth import (
    AuthUser,
    EdgeExchangeRequest,
    EdgeHandoffOut,
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    RegisterRequest,
    TokenPair,
)
from app.schemas.wyv import WyvHandoffOut
from app.services.legal import apply_current_legal_acceptance, user_requires_legal_reacceptance, validate_legal_acceptance_payload
from app.services.sync_bridge import (
    consume_edge_handoff_grant,
    create_edge_handoff_grant,
    decode_edge_handoff_grant,
    enqueue_upsert_event,
)
from app.services.rate_limiter import rate_limiter
from app.services.usernames import generate_discriminator
from app.services.wyv_bridge import create_wyv_handoff_grant, wyv_bridge_is_configured
from app.utils.dependencies import get_current_active_user
from app.utils.responses import success_response
from app.utils.security import (
    TokenError,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    hash_token,
    verify_password,
)


router = APIRouter(prefix="/auth", tags=["auth"])
settings = get_settings()


async def _throttle_auth(actor_key: str, key_prefix: str) -> None:
    await rate_limiter.check(
        key_prefix=key_prefix,
        actor_id=actor_key,
        limit=settings.rate_limit_auth_count,
        window_seconds=settings.rate_limit_auth_window_seconds,
    )


def _client_rate_key(request: Request) -> str:
    cf_ip = request.headers.get("CF-Connecting-IP")
    if cf_ip:
        return f"ip:{cf_ip.strip()}"
    client_host = request.client.host if request.client else "unknown"
    return f"ip:{client_host}"


async def _throttle_auth_request(request: Request, actor_key: str, key_prefix: str) -> None:
    await _throttle_auth(actor_key, key_prefix)
    await _throttle_auth(_client_rate_key(request), f"{key_prefix}.client")


def _serialize_auth_user(user: User) -> dict:
    payload = AuthUser.model_validate(user).model_dump(mode="json")
    payload["legal_reaccept_required"] = user_requires_legal_reacceptance(user)
    return payload


@router.post("/register")
async def register(payload: RegisterRequest, request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    await _throttle_auth_request(request, payload.email.lower(), "auth.register")
    try:
        validate_legal_acceptance_payload(
            accepted_legal=payload.accepted_legal,
            terms_version=payload.terms_version,
            privacy_version=payload.privacy_version,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    existing = await db.execute(select(User).where(func.lower(User.email) == payload.email.lower()))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already in use")

    discriminator = await generate_discriminator(db, payload.username)

    user = User(
        username=payload.username,
        discriminator=discriminator,
        display_name=(payload.display_name or payload.username),
        email=payload.email.lower(),
        password_hash=hash_password(payload.password),
    )
    db.add(user)
    await db.flush()
    apply_current_legal_acceptance(user)
    await enqueue_upsert_event(db, "user", user, base_sync_version=0)

    access_token = create_access_token(user.id)
    refresh_token, refresh_token_hash, refresh_expires_at = create_refresh_token(user.id)

    db.add(
        RefreshToken(
            user_id=user.id,
            token_hash=refresh_token_hash,
            expires_at=refresh_expires_at,
        )
    )

    await db.commit()
    await db.refresh(user)

    return success_response(
        {
            "user": _serialize_auth_user(user),
            "tokens": TokenPair(access_token=access_token, refresh_token=refresh_token).model_dump(),
        }
    )


@router.post("/login")
async def login(payload: LoginRequest, request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    await _throttle_auth_request(request, payload.email.lower(), "auth.login")
    result = await db.execute(select(User).where(func.lower(User.email) == payload.email.lower()))
    user = result.scalar_one_or_none()

    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    access_token = create_access_token(user.id)
    refresh_token, refresh_token_hash, refresh_expires_at = create_refresh_token(user.id)
    db.add(
        RefreshToken(
            user_id=user.id,
            token_hash=refresh_token_hash,
            expires_at=refresh_expires_at,
        )
    )
    await db.commit()

    return success_response(
        {
            "user": _serialize_auth_user(user),
            "tokens": TokenPair(access_token=access_token, refresh_token=refresh_token).model_dump(),
        }
    )


@router.post("/refresh")
async def refresh_tokens(payload: RefreshRequest, request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    await _throttle_auth_request(request, hash_token(payload.refresh_token), "auth.refresh")
    try:
        token_payload = decode_token(payload.refresh_token)
    except TokenError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    if token_payload.get("type") != "refresh":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token type")

    user_id = str(token_payload.get("sub") or "")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token subject")
    token_hash = hash_token(payload.refresh_token)

    result = await db.execute(
        select(RefreshToken).where(
            and_(
                RefreshToken.user_id == user_id,
                RefreshToken.token_hash == token_hash,
                RefreshToken.is_revoked.is_(False),
            )
        )
    )
    token_record = result.scalar_one_or_none()

    if token_record is None or token_record.expires_at < datetime.now(tz=UTC):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token is invalid or expired")

    token_record.is_revoked = True

    access_token = create_access_token(user_id)
    refresh_token, refresh_token_hash, refresh_expires_at = create_refresh_token(user_id)

    db.add(
        RefreshToken(
            user_id=user_id,
            token_hash=refresh_token_hash,
            expires_at=refresh_expires_at,
        )
    )

    await db.commit()

    return success_response(
        {
            "tokens": TokenPair(access_token=access_token, refresh_token=refresh_token).model_dump(),
        }
    )


@router.post("/logout")
async def logout(payload: LogoutRequest, db: AsyncSession = Depends(get_db)) -> dict:
    try:
        token_payload = decode_token(payload.refresh_token)
    except TokenError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    if token_payload.get("type") != "refresh":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token type")

    user_id = str(token_payload.get("sub") or "")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token subject")
    token_hash = hash_token(payload.refresh_token)

    result = await db.execute(
        select(RefreshToken).where(
            and_(
                RefreshToken.user_id == user_id,
                RefreshToken.token_hash == token_hash,
                RefreshToken.is_revoked.is_(False),
            )
        )
    )
    token_record = result.scalar_one_or_none()

    if token_record:
        token_record.is_revoked = True
        await db.commit()

    return success_response({"logged_out": True})


@router.post("/edge-handoff")
async def edge_handoff(current_user: User = Depends(get_current_active_user)) -> dict:
    if settings.node_role != "main" or not settings.edge_mode_enabled or not settings.sync_shared_secret:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Edge Mode is not configured")
    grant, expires_at = create_edge_handoff_grant(current_user)
    return success_response(EdgeHandoffOut(grant=grant, expires_at=expires_at).model_dump(mode="json"))


@router.post("/wyv-handoff")
async def wyv_handoff(current_user: User = Depends(get_current_active_user)) -> dict:
    if not wyv_bridge_is_configured():
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Wyv bridge is not configured")
    grant, expires_at = create_wyv_handoff_grant(current_user)
    return success_response(WyvHandoffOut(grant=grant, expires_at=expires_at).model_dump(mode="json"))


@router.post("/edge-exchange")
async def edge_exchange(payload: EdgeExchangeRequest, db: AsyncSession = Depends(get_db)) -> dict:
    if not settings.edge_mode_enabled or not settings.sync_shared_secret:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Edge Mode is not configured")

    try:
        grant_payload = decode_edge_handoff_grant(payload.grant)
        await consume_edge_handoff_grant(grant_payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    user_snapshot = grant_payload.get("user") or {}
    user_sync_id = str(user_snapshot.get("sync_id") or grant_payload.get("sub") or "")
    if not user_sync_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Edge handoff grant is missing a user id")

    result = await db.execute(select(User).where(User.sync_id == user_sync_id))
    user = result.scalar_one_or_none()

    if user is None and user_snapshot.get("email"):
        result = await db.execute(select(User).where(func.lower(User.email) == str(user_snapshot["email"]).lower()))
        user = result.scalar_one_or_none()

    if user is None:
        user = User(
            sync_id=user_sync_id,
            username=str(user_snapshot.get("username") or "edge-user"),
            discriminator=str(user_snapshot.get("discriminator") or "0001"),
            display_name=user_snapshot.get("display_name"),
            bio=user_snapshot.get("bio"),
            directory_opt_in=bool(user_snapshot.get("directory_opt_in")),
            email=str(user_snapshot.get("email") or f"{user_sync_id}@edge.invalid"),
            avatar=user_snapshot.get("avatar"),
            password_hash=hash_password(str(uuid4())),
            is_paid=False,
            accepted_terms_version=user_snapshot.get("accepted_terms_version"),
            accepted_privacy_version=user_snapshot.get("accepted_privacy_version"),
            legal_accepted_at=datetime.now(tz=UTC) if user_snapshot.get("accepted_terms_version") else None,
            ai_opt_in=bool(user_snapshot.get("ai_opt_in")),
            ai_opt_in_updated_at=datetime.now(tz=UTC) if user_snapshot.get("ai_opt_in") is not None else None,
            nsfw_18_verified=bool(user_snapshot.get("nsfw_18_verified")),
            nsfw_18_verified_at=datetime.now(tz=UTC) if user_snapshot.get("nsfw_18_verified") else None,
        )
        db.add(user)
        await db.flush()

    access_token = create_access_token(user.id)
    refresh_token, refresh_token_hash, refresh_expires_at = create_refresh_token(user.id)
    db.add(
        RefreshToken(
            user_id=user.id,
            token_hash=refresh_token_hash,
            expires_at=refresh_expires_at,
        )
    )
    await db.commit()

    return success_response(
        {
            "user": _serialize_auth_user(user),
            "tokens": TokenPair(access_token=access_token, refresh_token=refresh_token).model_dump(),
        }
    )
