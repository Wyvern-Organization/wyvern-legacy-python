from types import SimpleNamespace

from app.models.enums import ChannelType
from app.services.release_flags import resolve_feature_flags, resolve_release_channel
from app.services.sync_bridge import _edge_write_allowed


def test_resolve_release_channel() -> None:
    assert resolve_release_channel("stable") == "stable"
    assert resolve_release_channel("edge") == "edge"
    assert resolve_release_channel(" EDGE ") == "edge"


def test_resolve_feature_flags_by_channel() -> None:
    flags = [
        SimpleNamespace(key="edge_release_banner", stable_enabled=False, edge_enabled=True),
        SimpleNamespace(key="stable_banner", stable_enabled=True, edge_enabled=True),
    ]

    stable_flags = resolve_feature_flags(flags, "stable")
    edge_flags = resolve_feature_flags(flags, "edge")

    assert stable_flags["edge_release_banner"] is False
    assert edge_flags["edge_release_banner"] is True
    assert stable_flags["stable_banner"] is True
    assert edge_flags["stable_banner"] is True


def test_edge_write_allowlist() -> None:
    assert _edge_write_allowed("message", "upsert", {})[0] is True
    assert _edge_write_allowed("reaction", "delete", {})[0] is True
    assert _edge_write_allowed("dm_participant", "upsert", {})[0] is True
    assert _edge_write_allowed("release_flag", "upsert", {})[0] is False

    assert _edge_write_allowed("channel", "upsert", {"type": ChannelType.dm.value})[0] is True
    assert _edge_write_allowed("channel", "upsert", {"type": ChannelType.text.value})[0] is False
    assert _edge_write_allowed("channel", "delete", {"type": ChannelType.dm.value})[0] is True
