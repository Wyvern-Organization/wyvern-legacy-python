import secrets

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.database import get_db
from app.models import MemberRole, Server, ServerActivityLog, ServerInvite, ServerMember, User
from app.schemas.community import CommunityActivityOut
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
from app.services.community import record_server_activity
from app.services.recommendations import (
    TARGET_SERVER,
    mark_public_entity_stale,
    recommended_server_rankings,
    recommendations_enabled,
    record_recommendation_signal,
)
from app.services.realtime import broadcast_server_event, broadcast_server_member_event
from app.services.sync_bridge import bump_sync_version, enqueue_delete_event, enqueue_upsert_event
from app.utils.dependencies import get_current_active_user
from app.utils.responses import success_response


router = APIRouter(prefix="/servers", tags=["servers"])


async def _get_membership(db: AsyncSession, server_id: str, user_id: str) -> ServerMember | None:
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
    current_user: User = Depends(get_current_active_user),
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
    await mark_public_entity_stale(db, TARGET_SERVER, server.id)
    await record_server_activity(
        db,
        server_id=server.id,
        actor_user_id=current_user.id,
        action="server.created",
        target_type="server",
        target_id=str(server.id),
        metadata={"name": server.name},
    )
    await db.commit()
    await db.refresh(server)
    await broadcast_server_event(db, "created", server)
    await broadcast_server_member_event(db, "created", owner_member, user=current_user, server=server)

    return success_response(ServerOut.model_validate(server).model_dump())


@router.get("")
async def list_servers(db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_active_user)) -> dict:
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
    recommended: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> dict:
    if recommended and recommendations_enabled():
        ranked_servers = await recommended_server_rankings(db, current_user)
        server_ids = [str(item.item.id) for item in ranked_servers]
        member_counts = {}
        joined_ids = set()
        if server_ids:
            counts_result = await db.execute(
                select(ServerMember.server_id, func.count(ServerMember.user_id).label("member_count"))
                .where(ServerMember.server_id.in_(server_ids))
                .group_by(ServerMember.server_id)
            )
            member_counts = {str(server_id): int(member_count or 0) for server_id, member_count in counts_result.all()}
            joined_result = await db.execute(
                select(ServerMember.server_id).where(
                    ServerMember.server_id.in_(server_ids),
                    ServerMember.user_id == current_user.id,
                )
            )
            joined_ids = {str(server_id) for server_id in joined_result.scalars().all()}
        entries = []
        for item in ranked_servers:
            server_id = str(item.item.id)
            payload = ServerDirectoryOut(
                server=ServerOut.model_validate(item.item),
                member_count=member_counts.get(server_id, 0),
                joined=server_id in joined_ids,
            ).model_dump(mode="json")
            payload["recommendation_score"] = item.score
            payload["recommendation_reason"] = item.reason
            entries.append(payload)
        return success_response(entries)

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
async def get_server(server_id: str, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_active_user)) -> dict:
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
    server_id: str,
    payload: ServerUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
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
    if provided & {"name", "description", "directory_opt_in"}:
        await mark_public_entity_stale(db, TARGET_SERVER, server.id)
    base_sync_version = bump_sync_version(server)
    await enqueue_upsert_event(db, "server", server, base_sync_version=base_sync_version)
    await record_server_activity(
        db,
        server_id=server.id,
        actor_user_id=current_user.id,
        action="server.updated",
        target_type="server",
        target_id=str(server.id),
        metadata={"directory_opt_in": server.directory_opt_in, "name": server.name},
    )

    await db.commit()
    await db.refresh(server)
    await broadcast_server_event(db, "updated", server)
    return success_response(ServerOut.model_validate(server).model_dump())


@router.delete("/{server_id}")
async def delete_server(
    server_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> dict:
    result = await db.execute(select(Server).where(Server.id == server_id))
    server = result.scalar_one_or_none()
    if server is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")

    if server.owner_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only owner can delete server")

    recipient_ids_result = await db.execute(select(ServerMember.user_id).where(ServerMember.server_id == server_id))
    recipient_ids = {str(user_id) for user_id in recipient_ids_result.scalars().all()}
    await enqueue_delete_event(db, "server", server, base_sync_version=server.sync_version)
    await record_server_activity(
        db,
        server_id=server.id,
        actor_user_id=current_user.id,
        action="server.deleted",
        target_type="server",
        target_id=str(server.id),
        metadata={"name": server.name},
    )
    await db.delete(server)
    await db.commit()
    await broadcast_server_event(db, "deleted", server, recipients=recipient_ids, extra_user_ids={current_user.id})
    return success_response({"deleted": True})


@router.post("/{server_id}/join")
async def join_server(
    server_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
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
    await record_recommendation_signal(db, current_user.id, TARGET_SERVER, server_id, "server.joined", weight=2.0)
    await record_server_activity(
        db,
        server_id=server_id,
        actor_user_id=current_user.id,
        action="server.member.joined",
        target_type="member",
        target_id=str(current_user.id),
        metadata={"role": new_member.role.value if hasattr(new_member.role, "value") else str(new_member.role)},
    )
    await db.commit()
    await db.refresh(new_member)
    await broadcast_server_member_event(db, "created", new_member, user=current_user, server=server)

    return success_response(ServerMemberOut.model_validate(new_member).model_dump())


@router.post("/{server_id}/invites")
async def create_server_invite(
    server_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
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
    await record_server_activity(
        db,
        server_id=server_id,
        actor_user_id=current_user.id,
        action="server.invite.created",
        target_type="invite",
        target_id=invite.code,
        metadata={"invite_path": _invite_path(invite.code)},
    )
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
    current_user: User = Depends(get_current_active_user),
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
    current_user: User = Depends(get_current_active_user),
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
        await record_recommendation_signal(db, current_user.id, TARGET_SERVER, server.id, "server.joined", weight=2.0)
        await record_server_activity(
            db,
            server_id=server.id,
            actor_user_id=current_user.id,
            action="server.member.joined",
            target_type="member",
            target_id=str(current_user.id),
            metadata={"via": "invite", "invite_code": invite.code},
        )
        await db.commit()
        await db.refresh(membership)
        await broadcast_server_member_event(db, "created", membership, user=current_user, server=server)
    payload = ServerJoinByInviteOut(
        server=ServerOut.model_validate(server),
        membership=ServerMemberOut.model_validate(membership),
    )
    return success_response(payload.model_dump(mode="json"))


@router.post("/{server_id}/leave")
async def leave_server(
    server_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> dict:
    membership = await _get_membership(db, server_id, current_user.id)
    if membership is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Membership not found")

    if membership.role == MemberRole.owner:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Owner cannot leave server")

    recipient_ids_result = await db.execute(select(ServerMember.user_id).where(ServerMember.server_id == server_id))
    recipient_ids = {str(user_id) for user_id in recipient_ids_result.scalars().all()}
    await enqueue_delete_event(db, "server_member", membership, base_sync_version=membership.sync_version)
    await record_server_activity(
        db,
        server_id=server_id,
        actor_user_id=current_user.id,
        action="server.member.left",
        target_type="member",
        target_id=str(current_user.id),
        metadata={"role": membership.role.value if hasattr(membership.role, "value") else str(membership.role)},
    )
    await db.delete(membership)
    await db.commit()
    result = await db.execute(select(Server).where(Server.id == server_id))
    server = result.scalar_one_or_none()
    if server is not None:
        await broadcast_server_member_event(db, "deleted", membership, user=current_user, server=server, recipients=recipient_ids, extra_user_ids={current_user.id})
    return success_response({"left": True})


@router.get("/{server_id}/members")
async def list_members(
    server_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
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
    server_id: str,
    member_user_id: str,
    role: MemberRole,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
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
    await record_server_activity(
        db,
        server_id=server_id,
        actor_user_id=current_user.id,
        action="server.member.role_updated",
        target_type="member",
        target_id=str(member_user_id),
        metadata={"role": role.value if hasattr(role, "value") else str(role)},
    )
    await db.commit()
    await db.refresh(target_membership)
    target_user = await db.get(User, member_user_id)
    await broadcast_server_member_event(db, "updated", target_membership, user=target_user, server=await db.get(Server, server_id))
    return success_response(ServerMemberOut.model_validate(target_membership).model_dump())


@router.get("/{server_id}/activity")
async def server_activity(
    server_id: str,
    limit: int = Query(default=50, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> dict:
    membership = await _get_membership(db, server_id, current_user.id)
    if membership is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not a member")

    result = await db.execute(
        select(ServerActivityLog)
        .where(ServerActivityLog.server_id == server_id)
        .order_by(desc(ServerActivityLog.created_at), desc(ServerActivityLog.id))
        .limit(limit)
    )
    items = [CommunityActivityOut.model_validate(item).model_dump(mode="json") for item in result.scalars().all()]
    return success_response({"items": items})
