from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import ChannelReadState, User
from app.schemas.read_state import ChannelReadStateOut
from app.services.legal import user_requires_legal_reacceptance
from app.schemas.user import PresenceOut, PresenceUpdateRequest, UserMeOut, UserOut
from app.services.admin_allowlist import admin_allowlist_service
from app.services.live_bridge import queue_realtime_event
from app.services.presence import presence_service
from app.services.recommendations import mark_public_entity_stale, recommended_user_rankings, recommendations_enabled
from app.services.realtime import broadcast_presence_update, broadcast_public_user_update
from app.services.sync_bridge import bump_sync_version, enqueue_upsert_event
from app.services.usernames import generate_discriminator, username_discriminator_taken
from app.utils.dependencies import get_current_active_user, get_current_user
from app.utils.responses import success_response
from app.utils.validation import validate_public_url, validate_username_handle


router = APIRouter(prefix="/users", tags=["users"])


class UserUpdateRequest(BaseModel):
    avatar: str | None = None
    username: str | None = Field(default=None, min_length=2, max_length=32)
    display_name: str | None = Field(default=None, min_length=1, max_length=64)
    bio: str | None = Field(default=None, max_length=280)
    directory_opt_in: bool | None = None
    ai_opt_in: bool | None = None
    nsfw_18_verified: bool | None = None

    @field_validator("avatar")
    @classmethod
    def validate_avatar(cls, value: str | None) -> str | None:
        return validate_public_url(value, field_name="avatar")

    @field_validator("username")
    @classmethod
    def validate_username(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_username_handle(value)


async def build_current_user_payload(current_user: User) -> dict:
    payload = UserMeOut.model_validate(current_user).model_dump()
    payload["is_admin"] = await admin_allowlist_service.is_admin(current_user.username, current_user.discriminator)
    payload["legal_reaccept_required"] = user_requires_legal_reacceptance(current_user)
    return payload


async def apply_username_update(db: AsyncSession, current_user: User, username: str) -> None:
    if await username_discriminator_taken(
        db,
        username,
        current_user.discriminator,
        exclude_user_id=current_user.id,
    ):
        current_user.discriminator = await generate_discriminator(db, username, exclude_user_id=current_user.id)
    current_user.username = username


@router.get("/me")
async def me(current_user: User = Depends(get_current_user)) -> dict:
    return success_response(await build_current_user_payload(current_user))


@router.get("/me/read-states")
async def my_read_states(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> dict:
    result = await db.execute(
        select(ChannelReadState)
        .where(ChannelReadState.user_id == current_user.id)
        .order_by(ChannelReadState.updated_at.desc(), ChannelReadState.channel_id.asc())
    )
    payload = [
        ChannelReadStateOut(
            channel_id=state.channel_id,
            last_read_message_id=state.last_read_message_id,
            last_read_at=state.last_read_at,
            updated_at=state.updated_at,
        ).model_dump(mode="json")
        for state in result.scalars().all()
    ]
    return success_response(payload)


@router.patch("/me")
async def update_me(
    payload: UserUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> dict:
    provided = payload.model_fields_set

    if "avatar" in provided:
        current_user.avatar = payload.avatar
    if "username" in provided and payload.username is not None:
        await apply_username_update(db, current_user, payload.username)
    if "display_name" in provided:
        current_user.display_name = payload.display_name
    if "bio" in provided:
        current_user.bio = payload.bio
    if "directory_opt_in" in provided and payload.directory_opt_in is not None:
        current_user.directory_opt_in = payload.directory_opt_in
    if "ai_opt_in" in provided and payload.ai_opt_in is not None:
        current_user.ai_opt_in = payload.ai_opt_in
        current_user.ai_opt_in_updated_at = datetime.now(tz=UTC)
    if "nsfw_18_verified" in provided and payload.nsfw_18_verified is not None:
        current_user.nsfw_18_verified = payload.nsfw_18_verified
        current_user.nsfw_18_verified_at = datetime.now(tz=UTC) if payload.nsfw_18_verified else None
    if provided & {"username", "display_name", "bio", "directory_opt_in"}:
        await mark_public_entity_stale(db, "user", current_user.id)
    base_sync_version = bump_sync_version(current_user)
    await enqueue_upsert_event(db, "user", current_user, base_sync_version=base_sync_version)
    await db.commit()
    await db.refresh(current_user)
    await broadcast_public_user_update(current_user, extra_user_ids={current_user.id})
    return success_response(await build_current_user_payload(current_user))


@router.get("/lookup")
async def lookup_user(
    q: str = Query(..., min_length=2, max_length=64, description="username or username#1234"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_active_user),
) -> dict:
    query_value = q.strip()

    result = None
    if "#" in query_value:
        username_part, discriminator = query_value.rsplit("#", 1)
        username_part = username_part.strip()
        discriminator = discriminator.strip()
        if len(discriminator) == 4 and discriminator.isdigit():
            result = await db.execute(
                select(User).where(
                    func.lower(User.username) == username_part.lower(),
                    User.discriminator == discriminator,
                )
            )
    else:
        lowered = query_value.lower()
        result = await db.execute(
            select(User)
            .where(
                or_(
                    func.lower(User.username) == lowered,
                    func.lower(func.coalesce(User.display_name, "")) == lowered,
                )
            )
            .order_by(User.id.asc())
            .limit(1)
        )

    user = result.scalar_one_or_none() if result is not None else None
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    return success_response(UserOut.model_validate(user).model_dump())


@router.get("/directory")
async def user_directory(
    recommended: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> dict:
    if recommended and recommendations_enabled():
        ranked_users = await recommended_user_rankings(db, current_user)
        payload = []
        for item in ranked_users:
            user_payload = UserOut.model_validate(item.item).model_dump()
            user_payload["recommendation_score"] = item.score
            user_payload["recommendation_reason"] = item.reason
            payload.append(user_payload)
        return success_response(payload)

    result = await db.execute(
        select(User)
        .where(User.directory_opt_in.is_(True))
        .order_by(
            func.lower(func.coalesce(User.display_name, User.username)).asc(),
            User.id.asc(),
        )
    )
    users = result.scalars().all()
    return success_response([UserOut.model_validate(user).model_dump() for user in users])


@router.get("/{user_id}")
async def get_user(user_id: str, db: AsyncSession = Depends(get_db), _: User = Depends(get_current_active_user)) -> dict:
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return success_response(UserOut.model_validate(user).model_dump())


@router.put("/me/presence")
async def set_presence(payload: PresenceUpdateRequest, current_user: User = Depends(get_current_active_user)) -> dict:
    normalized = await presence_service.set_presence(current_user.id, payload.status)
    await broadcast_presence_update(current_user.id, normalized)
    queue_realtime_event({"kind": "presence", "user_id": current_user.id, "status": normalized.value})
    return success_response(PresenceOut(user_id=current_user.id, status=payload.status).model_dump())


@router.get("/{user_id}/presence")
async def get_presence(user_id: str, _: User = Depends(get_current_active_user)) -> dict:
    status_value = await presence_service.get_presence(user_id)
    return success_response(PresenceOut(user_id=user_id, status=status_value).model_dump())
