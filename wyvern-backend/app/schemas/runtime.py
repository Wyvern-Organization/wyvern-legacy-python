from pydantic import BaseModel

from app.schemas.release import BridgeHealthOut


class RuntimeConfigOut(BaseModel):
    backend_url: str | None
    client_mode: str
    release_channel: str
    node_role: str
    node_id: str
    indexing: bool = False
    edge_mode_enabled: bool
    sync_peer_api_url: str | None
    edge_mode_available: bool
    bridge_schema_version: int
    sync_enabled: bool
    feature_flags: dict[str, bool]
    giphy_api_key: str | None = None
    giphy_rating: str = "g"
    giphy_limit: int = 24
    bridge_health: BridgeHealthOut | None = None
