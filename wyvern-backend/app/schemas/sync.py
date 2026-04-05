from pydantic import BaseModel, Field


class ReplicationEventIn(BaseModel):
    event_id: str = Field(min_length=1, max_length=36)
    schema_version: int
    source_node: str = Field(min_length=1, max_length=16)
    entity_type: str = Field(min_length=1, max_length=64)
    action: str = Field(min_length=1, max_length=16)
    entity_sync_id: str = Field(min_length=1, max_length=36)
    base_sync_version: int | None = None
    payload: dict | None = None


class ReplicationBatchIn(BaseModel):
    events: list[ReplicationEventIn]
