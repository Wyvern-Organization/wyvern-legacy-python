from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.schemas.runtime import RuntimeConfigOut, UiVariantCatalogOut, UiVariantOut, UiVariantVoteRequest
from app.services.legal import legal_metadata
from app.services.release_flags import build_bridge_health, load_release_flags, resolve_feature_flags, resolve_release_channel
from app.services.sync_bridge import BRIDGE_SCHEMA_VERSION, edge_mode_is_available
from app.services.ui_variants import (
    EDGE_UI_VARIANT_LABELS,
    EDGE_UI_VARIANT_POLL_KEY,
    EDGE_UI_VARIANT_ROUTES,
    edge_ui_variant_availability,
    get_ui_variant_vote_counts,
    get_user_ui_variant_vote,
    upsert_user_ui_variant_vote,
)
from app.utils.dependencies import get_current_active_user
from app.utils.responses import success_response
from app.models import User


router = APIRouter(tags=["runtime"])
settings = get_settings()
PROJECT_ROOT = Path(__file__).resolve().parents[2]


@router.get("/runtime-config")
async def runtime_config(request: Request, mode: str = Query(default="stable"), db: AsyncSession = Depends(get_db)) -> dict:
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
        wyv_public_base_url=settings.wyv_public_base_url,
        feature_flags=resolve_feature_flags(flags, release_channel),
        giphy_api_key=settings.giphy_api_key,
        giphy_rating=settings.giphy_rating,
        giphy_limit=settings.giphy_limit,
        bridge_health=await build_bridge_health(db),
        legal=legal_metadata(str(request.base_url).rstrip("/")),
    )
    return success_response(payload.model_dump())


def _build_ui_variant_catalog_payload(
    current_vote: str | None,
    counts: dict[str, int],
) -> UiVariantCatalogOut:
    availability = edge_ui_variant_availability(PROJECT_ROOT)
    return UiVariantCatalogOut(
        poll_key=EDGE_UI_VARIANT_POLL_KEY,
        current_vote=current_vote,
        variants=[
            UiVariantOut(
                key=key,
                label=EDGE_UI_VARIANT_LABELS[key],
                route=EDGE_UI_VARIANT_ROUTES[key],
                available=bool(availability.get(key)),
                vote_count=int(counts.get(key, 0)),
                current_user_vote=current_vote == key,
            )
            for key in ("original", "ui_a", "ui_b")
        ],
    )


@router.get("/runtime/ui-variants")
async def runtime_ui_variants(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> dict:
    counts = await get_ui_variant_vote_counts(db)
    vote = await get_user_ui_variant_vote(db, current_user.id)
    payload = _build_ui_variant_catalog_payload(vote.variant_key if vote else None, counts)
    return success_response(payload.model_dump(mode="json"))


@router.put("/runtime/ui-variants/vote")
async def runtime_ui_variant_vote(
    body: UiVariantVoteRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> dict:
    variant_key = str(body.variant_key)
    availability = edge_ui_variant_availability(PROJECT_ROOT)
    if not availability.get(variant_key):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"UI variant '{variant_key}' is not available on this host",
        )

    await upsert_user_ui_variant_vote(db, current_user.id, variant_key)
    counts = await get_ui_variant_vote_counts(db)
    payload = _build_ui_variant_catalog_payload(variant_key, counts)
    return success_response(payload.model_dump(mode="json"))
