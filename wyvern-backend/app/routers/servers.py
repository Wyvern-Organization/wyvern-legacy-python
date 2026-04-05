import secrets

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.database import get_db
from app.models import MemberRole, Server, ServerInvite, ServerMember, User
from app.schemas.server import (
    ServerCreate,
    ServerDirectoryOut,
    ServerInviteLookupOut,
    ServerInviteOut,
    ServerJoinByInviteOut,
    ServerMemberOut,
    ServerOut,
    ServerUpdate,
)
from app.services.sync_bridge import bump_sync_version, enqueue_delete_event, enqueue_upsert_event
from app.utils.dependencies import get_current_user
from app.utils.responses import success_response


router = APIRouter(prefix="/servers", tags=["servers"])


async def _get_membership(db: AsyncSession, server_id: int, user_id: int) -> ServerMember | None:
    result = await db.execute(
        select(ServerMember).where(and_(ServerMember.server_id == server_id, ServerMember.user_id == user_id))
    )
    return result.scalar_one_or_none()


def _can_manage_server(role: MemberRole) -> bool:
    return role in {MemberRole.owner, MemberRole.admin}


async def _generate_unique_invite_code(db: AsyncSession) -> str:
    for _ in range(16):
        code = secrets.token_urlsafe(9).replace("-", "").replace("_", "")[:12]
        if not code:
            continue
        existing = await db.execute(select(ServerInvite.id).where(ServerInvite.code == code))
        if existing.scalar_one_or_none() is None:
            return code
    raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to generate invite code")


def _invite_path(code: str) -> str:
    return f"/invite/{code}"


@router.post("")
async def create_server(
    payload: ServerCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    server = Server(
        name=payload.name,
        description=payload.description,
        icon=payload.icon,
        directory_opt_in=payload.directory_opt_in,
        owner_id=current_user.id,
    )
    db.add(server)
    await db.flush()

    db.add(
        ServerMember(
            server_id=server.id,
            user_id=current_user.id,
            role=MemberRole.owner,
        )
    )
    await db.flush()
    owner_member = (
        await db.execute(
            select(ServerMember).where(and_(ServerMember.server_id == server.id, ServerMember.user_id == current_user.id))
        )
    ).scalar_one()
    await enqueue_upsert_event(db, "server", server, base_sync_version=0)
    await enqueue_upsert_event(db, "server_member", owner_member, base_sync_version=0)
    await db.commit()
    await db.refresh(server)

    return success_response(ServerOut.model_validate(server).model_dump())


@router.get("")
async def list_servers(db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict:
    result = await db.execute(
        select(Server)
        .join(ServerMember, ServerMember.server_id == Server.id)
        .where(ServerMember.user_id == current_user.id)
        .order_by(Server.created_at.desc())
    )
    servers = result.scalars().all()
    return success_response([ServerOut.model_validate(server).model_dump() for server in servers])


@router.get("/directory")
async def list_server_directory(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    member_counts = (
        select(
            ServerMember.server_id.label("server_id"),
            func.count(ServerMember.user_id).label("member_count"),
        )
        .group_by(ServerMember.server_id)
        .subquery()
    )
    joined_member = aliased(ServerMember)

    result = await db.execute(
        select(
            Server,
            func.coalesce(member_counts.c.member_count, 0).label("member_count"),
            joined_member.user_id.label("joined_user_id"),
        )
        .outerjoin(member_counts, member_counts.c.server_id == Server.id)
        .outerjoin(
            joined_member,
            and_(joined_member.server_id == Server.id, joined_member.user_id == current_user.id),
        )
        .where(Server.directory_opt_in.is_(True))
        .order_by(func.lower(Server.name).asc(), Server.id.asc())
    )
    entries = []
    for server, member_count, joined_user_id in result.all():
        entries.append(
            ServerDirectoryOut(
                server=ServerOut.model_validate(server),
                member_count=int(member_count or 0),
                joined=joined_user_id is not None,
            ).model_dump(mode="json")
        )
    return success_response(entries)


@router.get("/{server_id}")
async def get_server(server_id: int, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict:
    result = await db.execute(select(Server).where(Server.id == server_id))
    server = result.scalar_one_or_none()
    if server is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")

    membership = await _get_membership(db, server_id, current_user.id)
    if membership is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not a member of this server")

    return success_response(ServerOut.model_validate(server).model_dump())


@router.patch("/{server_id}")
async def update_server(
    server_id: int,
    payload: ServerUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    result = await db.execute(select(Server).where(Server.id == server_id))
    server = result.scalar_one_or_none()
    if server is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")

    membership = await _get_membership(db, server_id, current_user.id)
    if membership is None or not _can_manage_server(membership.role):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")

    provided = payload.model_fields_set

    if "name" in provided and payload.name is not None:
        server.name = payload.name
    if "description" in provided:
        server.description = payload.description
    if "icon" in provided:
        server.icon = payload.icon
    if "directory_opt_in" in provided and payload.directory_opt_in is not None:
        server.directory_opt_in = payload.directory_opt_in
    base_sync_version = bump_sync_version(server)
    await enqueue_upsert_event(db, "server", server, base_sync_version=base_sync_version)

    await db.commit()
    await db.refresh(server)
    return success_response(ServerOut.model_validate(server).model_dump())


@router.delete("/{server_id}")
async def delete_server(
    server_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    result = await db.execute(select(Server).where(Server.id == server_id))
    server = result.scalar_one_or_none()
    if server is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")

    if server.owner_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only owner can delete server")

    await enqueue_delete_event(db, "server", server, base_sync_version=server.sync_version)
    await db.delete(server)
    await db.commit()
    return success_response({"deleted": True})


@router.post("/{server_id}/join")
async def join_server(
    server_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    result = await db.execute(select(Server).where(Server.id == server_id))
    server = result.scalar_one_or_none()
    if server is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")

    membership = await _get_membership(db, server_id, current_user.id)
    if membership is not None:
        return success_response(ServerMemberOut.model_validate(membership).model_dump())

    new_member = ServerMember(server_id=server_id, user_id=current_user.id, role=MemberRole.member)
    db.add(new_member)
    await db.flush()
    await enqueue_upsert_event(db, "server_member", new_member, base_sync_version=0)
    await db.commit()
    await db.refresh(new_member)

    return success_response(ServerMemberOut.model_validate(new_member).model_dump())


@router.post("/{server_id}/invites")
async def create_server_invite(
    server_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    server_result = await db.execute(select(Server).where(Server.id == server_id))
    server = server_result.scalar_one_or_none()
    if server is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")

    membership = await _get_membership(db, server_id, current_user.id)
    if membership is None or not _can_manage_server(membership.role):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")

    code = await _generate_unique_invite_code(db)
    invite = ServerInvite(server_id=server_id, code=code, created_by=current_user.id)
    db.add(invite)
    await db.flush()
    await enqueue_upsert_event(db, "server_invite", invite, base_sync_version=0)
    await db.commit()
    await db.refresh(invite)

    payload = ServerInviteOut(
        code=invite.code,
        server_id=invite.server_id,
        created_by=invite.created_by,
        created_at=invite.created_at,
        invite_path=_invite_path(invite.code),
    )
    return success_response(payload.model_dump(mode="json"))


@router.get("/invites/{code}")
async def lookup_server_invite(
    code: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    _ = current_user
    result = await db.execute(select(ServerInvite).where(ServerInvite.code == code))
    invite = result.scalar_one_or_none()
    if invite is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invite not found")

    server_result = await db.execute(select(Server).where(Server.id == invite.server_id))
    server = server_result.scalar_one_or_none()
    if server is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")

    payload = ServerInviteLookupOut(
        code=invite.code,
        server=ServerOut.model_validate(server),
    )
    return success_response(payload.model_dump(mode="json"))


@router.post("/invites/{code}/join")
async def join_server_by_invite(
    code: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    invite_result = await db.execute(select(ServerInvite).where(ServerInvite.code == code))
    invite = invite_result.scalar_one_or_none()
    if invite is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invite not found")

    server_result = await db.execute(select(Server).where(Server.id == invite.server_id))
    server = server_result.scalar_one_or_none()
    if server is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")

    membership = await _get_membership(db, server.id, current_user.id)
    if membership is None:
        membership = ServerMember(server_id=server.id, user_id=current_user.id, role=MemberRole.member)
        db.add(membership)
        await db.flush()
        await enqueue_upsert_event(db, "server_member", membership, base_sync_version=0)
        await db.commit()
        await db.refresh(membership)
    payload = ServerJoinByInviteOut(
        server=ServerOut.model_validate(server),
        membership=ServerMemberOut.model_validate(membership),
    )
    return success_response(payload.model_dump(mode="json"))


@router.post("/{server_id}/leave")
async def leave_server(
    server_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    membership = await _get_membership(db, server_id, current_user.id)
    if membership is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Membership not found")

    if membership.role == MemberRole.owner:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Owner cannot leave server")

    await enqueue_delete_event(db, "server_member", membership, base_sync_version=membership.sync_version)
    await db.delete(membership)
    await db.commit()
    return success_response({"left": True})


@router.get("/{server_id}/members")
async def list_members(
    server_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    membership = await _get_membership(db, server_id, current_user.id)
    if membership is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not a member")

    result = await db.execute(
        select(ServerMember).where(ServerMember.server_id == server_id).order_by(ServerMember.joined_at.asc())
    )
    members = result.scalars().all()
    return success_response([ServerMemberOut.model_validate(member).model_dump() for member in members])


@router.patch("/{server_id}/members/{member_user_id}")
async def update_member_role(
    server_id: int,
    member_user_id: int,
    role: MemberRole,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    acting_membership = await _get_membership(db, server_id, current_user.id)
    if acting_membership is None or acting_membership.role != MemberRole.owner:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only owner can change roles")

    target_membership = await _get_membership(db, server_id, member_user_id)
    if target_membership is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Target member not found")

    if target_membership.user_id == current_user.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot change owner role")

    target_membership.role = role
    base_sync_version = bump_sync_version(target_membership)
    await enqueue_upsert_event(db, "server_member", target_membership, base_sync_version=base_sync_version)
    await db.commit()
    await db.refresh(target_membership)
    return success_response(ServerMemberOut.model_validate(target_membership).model_dump())
