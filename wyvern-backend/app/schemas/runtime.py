from typing import Literal

from pydantic import BaseModel

from app.schemas.legal import LegalMetadataOut
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
    wyv_public_base_url: str | None = None
    feature_flags: dict[str, bool]
    giphy_api_key: str | None = None
    giphy_rating: str = "g"
    giphy_limit: int = 24
    bridge_health: BridgeHealthOut | None = None
    legal: LegalMetadataOut


class UiVariantOut(BaseModel):
    key: Literal["original", "ui_a", "ui_b"]
    label: str
    route: str
    available: bool
    vote_count: int = 0
    current_user_vote: bool = False


class UiVariantCatalogOut(BaseModel):
    poll_key: str
    current_vote: Literal["original", "ui_a", "ui_b"] | None = None
    variants: list[UiVariantOut]


class UiVariantVoteRequest(BaseModel):
    variant_key: Literal["original", "ui_a", "ui_b"]
