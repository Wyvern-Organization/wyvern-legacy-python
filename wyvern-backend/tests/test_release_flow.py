from types import SimpleNamespace

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.enums import ChannelType
from app.services.release_flags import (
    DEFAULT_RELEASE_FLAGS,
    count_promotable_release_flags,
    load_release_flags,
    resolve_feature_flags,
    resolve_release_channel,
)
from app.services.sync_bridge import _edge_write_allowed


def test_resolve_release_channel() -> None:
    assert resolve_release_channel("stable") == "stable"
    assert resolve_release_channel("edge") == "edge"
    assert resolve_release_channel(" EDGE ") == "edge"


def test_resolve_feature_flags_by_channel() -> None:
    flags = [
        SimpleNamespace(key="edge_release_banner", stable_enabled=True, edge_enabled=False),
        SimpleNamespace(key="stable_banner", stable_enabled=False, edge_enabled=True),
    ]

    stable_flags = resolve_feature_flags(flags, "stable")
    edge_flags = resolve_feature_flags(flags, "edge")

    assert stable_flags["edge_release_banner"] is False
    assert edge_flags["edge_release_banner"] is True
    assert stable_flags["stable_banner"] is False
    assert edge_flags["stable_banner"] is True
    assert count_promotable_release_flags(flags) == 1


def test_admin_diagnostics_flag_is_edge_default_and_promotable() -> None:
    flag = next(item for item in DEFAULT_RELEASE_FLAGS if item["key"] == "admin_diagnostics_button")

    assert flag["stable_enabled"] is False
    assert flag["edge_enabled"] is True

    flags = [
        SimpleNamespace(key=flag["key"], stable_enabled=flag["stable_enabled"], edge_enabled=flag["edge_enabled"])
    ]
    assert resolve_feature_flags(flags, "stable")[flag["key"]] is False
    assert resolve_feature_flags(flags, "edge")[flag["key"]] is True
    assert count_promotable_release_flags(flags) == 1


@pytest.mark.asyncio
async def test_load_release_flags_recovers_from_concurrent_default_seed() -> None:
    seeded_flags = [SimpleNamespace(key=item["key"]) for item in DEFAULT_RELEASE_FLAGS]

    class FakeResult:
        def __init__(self, flags):
            self._flags = flags

        def scalars(self):
            return self

        def all(self):
            return self._flags

    class FakeDB:
        def __init__(self):
            self.execute_count = 0
            self.rollback_count = 0

        async def execute(self, _stmt):
            self.execute_count += 1
            return FakeResult([] if self.execute_count == 1 else seeded_flags)

        def add(self, _flag):
            pass

        async def commit(self):
            raise IntegrityError("insert release flag", {}, Exception("duplicate key"))

        async def rollback(self):
            self.rollback_count += 1

    db = FakeDB()

    flags = await load_release_flags(db)

    assert {flag.key for flag in flags} == {item["key"] for item in DEFAULT_RELEASE_FLAGS}
    assert db.rollback_count == 1


def test_edge_write_allowlist() -> None:
    assert _edge_write_allowed("message", "upsert", {})[0] is True
    assert _edge_write_allowed("reaction", "delete", {})[0] is True
    assert _edge_write_allowed("dm_participant", "upsert", {})[0] is True
    assert _edge_write_allowed("release_flag", "upsert", {})[0] is False

    assert _edge_write_allowed("channel", "upsert", {"type": ChannelType.dm.value})[0] is True
    assert _edge_write_allowed("channel", "upsert", {"type": ChannelType.text.value})[0] is False
    assert _edge_write_allowed("channel", "delete", {"type": ChannelType.dm.value})[0] is True
