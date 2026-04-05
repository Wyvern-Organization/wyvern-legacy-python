from datetime import datetime

from pydantic import BaseModel, Field


class ReleaseFlagOut(BaseModel):
    key: str
    description: str
    stable_enabled: bool
    edge_enabled: bool
    channel_locked: bool = False
    updated_by_user_id: int | None = None
    updated_at: datetime
    last_promoted_at: datetime | None = None


class BridgeHealthOut(BaseModel):
    configured: bool
    node_role: str
    peer_url: str | None = None
    sync_enabled: bool
    edge_mode_enabled: bool
    edge_mode_available: bool
    pending_outbox: int
    dead_letter_outbox: int
    last_outbox_delivery_at: datetime | None = None
    last_inbound_at: datetime | None = None


class ReleaseStatusOut(BaseModel):
    release_channel: str
    flags: list[ReleaseFlagOut] = Field(default_factory=list)
    stable_feature_flags: dict[str, bool] = Field(default_factory=dict)
    edge_feature_flags: dict[str, bool] = Field(default_factory=dict)
    resolved_feature_flags: dict[str, bool] = Field(default_factory=dict)
    promotable_count: int = 0
    bridge_health: BridgeHealthOut | None = None


class ReleaseAuditOut(BaseModel):
    id: int
    promoted_by_user_id: int | None = None
    promoted_by_label: str | None = None
    promoted_at: datetime
    promoted_flag_keys: list[str] = Field(default_factory=list)


class ReleasePromotionOut(BaseModel):
    promoted_count: int
    promoted_flag_keys: list[str] = Field(default_factory=list)
    promoted_at: datetime
