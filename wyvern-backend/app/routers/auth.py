from datetime import UTC, datetime
import random

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import RefreshToken, User
from app.schemas.auth import AuthUser, LoginRequest, LogoutRequest, RefreshRequest, RegisterRequest, TokenPair
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


async def _generate_discriminator(db: AsyncSession, username: str) -> str:
    result = await db.execute(select(User.discriminator).where(User.username == username))
    used = {row[0] for row in result.all()}

    if len(used) >= 9999:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Username is unavailable")

    while True:
        candidate = f"{random.randint(1, 9999):04d}"
        if candidate not in used:
            return candidate


@router.post("/register")
async def register(payload: RegisterRequest, db: AsyncSession = Depends(get_db)) -> dict:
    existing = await db.execute(select(User).where(func.lower(User.email) == payload.email.lower()))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already in use")

    discriminator = await _generate_discriminator(db, payload.username)

    user = User(
        username=payload.username,
        discriminator=discriminator,
        display_name=(payload.display_name or payload.username),
        email=payload.email.lower(),
        password_hash=hash_password(payload.password),
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
    await db.refresh(user)

    return success_response(
        {
            "user": AuthUser.model_validate(user).model_dump(),
            "tokens": TokenPair(access_token=access_token, refresh_token=refresh_token).model_dump(),
        }
    )


@router.post("/login")
async def login(payload: LoginRequest, db: AsyncSession = Depends(get_db)) -> dict:
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
            "user": AuthUser.model_validate(user).model_dump(),
            "tokens": TokenPair(access_token=access_token, refresh_token=refresh_token).model_dump(),
        }
    )


@router.post("/refresh")
async def refresh_tokens(payload: RefreshRequest, db: AsyncSession = Depends(get_db)) -> dict:
    try:
        token_payload = decode_token(payload.refresh_token)
    except TokenError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    if token_payload.get("type") != "refresh":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token type")

    user_id = int(token_payload.get("sub", 0))
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

    user_id = int(token_payload.get("sub", 0))
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
