from fastapi import HTTPException, status
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Channel, ChannelType, DMParticipant, Message, Server, ServerMember


async def get_server_or_404(db: AsyncSession, server_id: int) -> Server:
    result = await db.execute(select(Server).where(Server.id == server_id))
    server = result.scalar_one_or_none()
    if server is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")
    return server


async def get_channel_or_404(db: AsyncSession, channel_id: int) -> Channel:
    result = await db.execute(select(Channel).where(Channel.id == channel_id))
    channel = result.scalar_one_or_none()
    if channel is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Channel not found")
    return channel


async def get_message_or_404(db: AsyncSession, message_id: int) -> Message:
    result = await db.execute(select(Message).where(Message.id == message_id))
    message = result.scalar_one_or_none()
    if message is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Message not found")
    return message


async def ensure_server_member(db: AsyncSession, server_id: int, user_id: int) -> ServerMember:
    result = await db.execute(
        select(ServerMember).where(and_(ServerMember.server_id == server_id, ServerMember.user_id == user_id))
    )
    membership = result.scalar_one_or_none()
    if membership is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User is not a server member")
    return membership


async def ensure_channel_access(db: AsyncSession, channel: Channel, user_id: int) -> None:
    if channel.type == ChannelType.dm:
        participant = await db.execute(
            select(DMParticipant).where(
                and_(DMParticipant.channel_id == channel.id, DMParticipant.user_id == user_id)
            )
        )
        if participant.scalar_one_or_none() is None:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No DM access")
        return

    if channel.server_id is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid channel configuration")

    await ensure_server_member(db, channel.server_id, user_id)
