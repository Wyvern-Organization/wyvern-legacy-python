from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.routers import servers, users
from app.services import recommendations


def _user(user_id: str, *, public: bool = True, bio: str | None = "likes research") -> SimpleNamespace:
    return SimpleNamespace(
        id=user_id,
        username=f"user{user_id}",
        discriminator="1234",
        display_name=f"User {user_id}",
        bio=bio,
        directory_opt_in=public,
        email=f"{user_id}@example.com",
        avatar=None,
        is_paid=False,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def _server(server_id: str, *, public: bool = True, description: str | None = "research lab") -> SimpleNamespace:
    return SimpleNamespace(
        id=server_id,
        name=f"Server {server_id}",
        description=description,
        icon=None,
        directory_opt_in=public,
        owner_id="owner_1",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def test_public_embedding_sources_exclude_private_entities_and_private_content() -> None:
    assert recommendations.build_user_embedding_source(_user("private", public=False)) is None
    assert recommendations.build_server_embedding_source(_server("private", public=False), ["general"]) is None

    server = _server("public", public=True)
    server.private_workspace_content = "secret workspace notes"
    source = recommendations.build_server_embedding_source(server, ["general", "announcements"])

    assert source is not None
    assert "Server public" in source
    assert "general" in source
    assert "secret workspace notes" not in source


def test_user_ranking_keeps_public_filters_hard() -> None:
    current = _user("current", public=True)
    public_match = _user("match", public=True)
    private_match = _user("private", public=False)
    rankings = recommendations.rank_user_candidates(
        current_user_id=current.id,
        candidates=[current, public_match, private_match],
        interest_embedding=[1.0, 0.0],
        candidate_embeddings={"match": [1.0, 0.0], "private": [1.0, 0.0]},
        mutual_counts={"match": 2, "private": 3},
        signal_scores={"match": 2.0, "private": 5.0},
    )

    assert [item.item.id for item in rankings] == ["match"]
    assert rankings[0].reason == "Similar interests"


def test_server_ranking_excludes_joined_and_private_servers() -> None:
    public_match = _server("match", public=True)
    joined_match = _server("joined", public=True)
    private_match = _server("private", public=False)
    rankings = recommendations.rank_server_candidates(
        candidates=[public_match, joined_match, private_match],
        joined_server_ids={"joined"},
        interest_embedding=[0.0, 1.0],
        candidate_embeddings={"match": [0.0, 1.0], "joined": [0.0, 1.0], "private": [0.0, 1.0]},
        member_counts={"match": 10, "joined": 1, "private": 50},
        activity_counts={"match": 4},
    )

    assert [item.item.id for item in rankings] == ["match"]
    assert rankings[0].reason == "Similar interests"


def test_refresh_due_uses_active_and_normal_windows() -> None:
    now = datetime(2026, 5, 7, 12, 0, tzinfo=UTC)

    assert recommendations.refresh_due(
        refreshed_at=now - timedelta(hours=7),
        last_activity_at=now - timedelta(hours=1),
        current_time=now,
        active_hours=6,
        normal_hours=24,
    )
    assert not recommendations.refresh_due(
        refreshed_at=now - timedelta(hours=7),
        last_activity_at=now - timedelta(hours=20),
        current_time=now,
        active_hours=6,
        normal_hours=24,
    )
    assert recommendations.refresh_due(
        refreshed_at=now - timedelta(hours=25),
        last_activity_at=now - timedelta(hours=20),
        current_time=now,
        active_hours=6,
        normal_hours=24,
    )


@pytest.mark.asyncio
async def test_recommendation_refresh_is_disabled_without_feature_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(recommendations, "recommendations_enabled", lambda: False)

    assert await recommendations.refresh_public_entity_embedding("server", "server_1") is False


class FakeScalars:
    def __init__(self, values):
        self.values = values

    def all(self):
        return self.values


class FakeResult:
    def __init__(self, values):
        self.values = values

    def scalars(self):
        return FakeScalars(self.values)

    def all(self):
        return self.values


class FakeDb:
    def __init__(self, values):
        self.values = values

    async def execute(self, _query):
        return FakeResult(self.values)


class FakeDbQueue:
    def __init__(self, values):
        self.values = list(values)

    async def execute(self, _query):
        return FakeResult(self.values.pop(0))


@pytest.mark.asyncio
async def test_user_directory_default_does_not_use_recommendation_service(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fail_if_called(*_args, **_kwargs):
        raise AssertionError("recommended ranking should not run for the default directory")

    monkeypatch.setattr(users, "recommended_user_rankings", fail_if_called)
    payload = await users.user_directory(
        recommended=False,
        db=FakeDb([_user("a"), _user("b")]),
        current_user=_user("current"),
    )

    assert [item["id"] for item in payload["data"]] == ["a", "b"]
    assert "recommendation_score" not in payload["data"][0]


@pytest.mark.asyncio
async def test_user_directory_recommended_adds_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    ranked_user = _user("match")

    async def fake_rankings(*_args, **_kwargs):
        return [
            recommendations.RankedRecommendation(
                item=ranked_user,
                score=0.9,
                reason="Similar interests",
                factors={"semantic": 0.9},
            )
        ]

    monkeypatch.setattr(users, "recommendations_enabled", lambda: True)
    monkeypatch.setattr(users, "recommended_user_rankings", fake_rankings)

    payload = await users.user_directory(
        recommended=True,
        db=FakeDb([]),
        current_user=_user("current"),
    )

    assert payload["data"][0]["id"] == "match"
    assert payload["data"][0]["recommendation_score"] == 0.9
    assert payload["data"][0]["recommendation_reason"] == "Similar interests"


@pytest.mark.asyncio
async def test_server_directory_recommended_adds_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    ranked_server = _server("server_match")

    async def fake_rankings(*_args, **_kwargs):
        return [
            recommendations.RankedRecommendation(
                item=ranked_server,
                score=0.75,
                reason="Active public community",
                factors={"activity": 0.7},
            )
        ]

    monkeypatch.setattr(servers, "recommendations_enabled", lambda: True)
    monkeypatch.setattr(servers, "recommended_server_rankings", fake_rankings)

    payload = await servers.list_server_directory(
        recommended=True,
        db=FakeDbQueue([[("server_match", 12)], []]),
        current_user=_user("current"),
    )

    assert payload["data"][0]["server"]["id"] == "server_match"
    assert payload["data"][0]["member_count"] == 12
    assert payload["data"][0]["recommendation_score"] == 0.75
    assert payload["data"][0]["recommendation_reason"] == "Active public community"
