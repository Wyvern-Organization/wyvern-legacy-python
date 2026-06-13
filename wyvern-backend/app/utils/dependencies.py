from collections.abc import AsyncIterator

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import User
from app.services.legal import (
    LEGAL_RECONSENT_ERROR_CODE,
    LEGAL_RECONSENT_MESSAGE,
    current_legal_versions,
    user_requires_legal_reacceptance,
)
from app.utils.security import TokenError, decode_token


oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login")


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    try:
        payload = decode_token(token)
    except TokenError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    token_type = payload.get("type")
    if token_type != "access":  # nosec B105
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token type")

    user_id = payload.get("sub")
    if user_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token subject")

    result = await db.execute(select(User).where(User.id == str(user_id)))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    return user


async def get_current_active_user(
    current_user: User = Depends(get_current_user),
) -> User:
    if user_requires_legal_reacceptance(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": LEGAL_RECONSENT_ERROR_CODE,
                "message": LEGAL_RECONSENT_MESSAGE,
                "details": current_legal_versions(),
            },
        )
    return current_user


async def get_db_session() -> AsyncIterator[AsyncSession]:
    async for session in get_db():
        yield session
