import random

from fastapi import HTTPException, status
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User


async def generate_discriminator(db: AsyncSession, username: str, *, exclude_user_id: str | None = None) -> str:
    conditions = [User.username == username]
    if exclude_user_id is not None:
        conditions.append(User.id != exclude_user_id)
    result = await db.execute(select(User.discriminator).where(and_(*conditions)))
    used = {row[0] for row in result.all()}

    if len(used) >= 9999:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Username is unavailable")

    while True:
        candidate = f"{random.randint(1, 9999):04d}"
        if candidate not in used:
            return candidate


async def username_discriminator_taken(
    db: AsyncSession,
    username: str,
    discriminator: str,
    *,
    exclude_user_id: str | None = None,
) -> bool:
    conditions = [
        User.username == username,
        User.discriminator == discriminator,
    ]
    if exclude_user_id is not None:
        conditions.append(User.id != exclude_user_id)
    result = await db.execute(select(User.id).where(and_(*conditions)).limit(1))
    return result.scalar_one_or_none() is not None
