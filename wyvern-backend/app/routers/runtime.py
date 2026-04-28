from fastapi import APIRouter, Depends, Query

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.schemas.runtime import RuntimeConfigOut
from app.services.release_flags import build_bridge_health, load_release_flags, resolve_feature_flags, resolve_release_channel
from app.services.sync_bridge import BRIDGE_SCHEMA_VERSION, edge_mode_is_available
from app.utils.responses import success_response


router = APIRouter(tags=["runtime"])
settings = get_settings()


@router.get("/runtime-config")
async def runtime_config(mode: str = Query(default="stable"), db: AsyncSession = Depends(get_db)) -> dict:
    requested_mode = mode.strip().lower()
    client_mode = "edge" if requested_mode == "edge" and settings.edge_mode_enabled else "stable"
    release_channel = resolve_release_channel(client_mode)
    flags = await load_release_flags(db)
    payload = RuntimeConfigOut(
        backend_url=None,
        client_mode=client_mode,
        release_channel=release_channel,
        node_role=settings.node_role,
        node_id=settings.node_id,
        indexing=settings.indexing,
        edge_mode_enabled=settings.edge_mode_enabled,
        sync_peer_api_url=settings.sync_peer_api_url,
        edge_mode_available=edge_mode_is_available(),
        bridge_schema_version=BRIDGE_SCHEMA_VERSION,
        sync_enabled=settings.sync_enabled,
        feature_flags=resolve_feature_flags(flags, release_channel),
        giphy_api_key=settings.giphy_api_key,
        giphy_rating=settings.giphy_rating,
        giphy_limit=settings.giphy_limit,
        bridge_health=await build_bridge_health(db),
    )
    return success_response(payload.model_dump())
