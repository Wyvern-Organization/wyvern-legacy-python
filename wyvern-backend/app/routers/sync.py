from fastapi import APIRouter, HTTPException, Request, status

from app.config import get_settings
from app.schemas.sync import ReplicationBatchIn
from app.services.sync_bridge import apply_replication_events, bridge_is_configured, build_bootstrap_events, verify_bridge_signature
from app.utils.responses import success_response


router = APIRouter(prefix="/internal/sync", tags=["sync"])
settings = get_settings()


def _verify_bridge_request(request: Request, body: bytes) -> None:
    if not bridge_is_configured():
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Sync bridge is not configured")
    try:
        verify_bridge_signature(
            body,
            request.headers.get("X-Wyvern-Bridge-Timestamp"),
            request.headers.get("X-Wyvern-Bridge-Signature"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc


@router.post("/batch")
async def apply_sync_batch(request: Request) -> dict:
    raw_body = await request.body()
    _verify_bridge_request(request, raw_body)
    payload = ReplicationBatchIn.model_validate_json(raw_body)
    results = await apply_replication_events([item.model_dump() for item in payload.events])
    return success_response({"results": results})


@router.post("/bootstrap")
async def bootstrap_sync_snapshot(request: Request) -> dict:
    raw_body = await request.body()
    _verify_bridge_request(request, raw_body)
    if settings.node_role != "main":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Bootstrap snapshots are only available from main")
    events = await build_bootstrap_events()
    return success_response({"events": events})
