from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.database import get_db
from app.main import app
from app.models import UiVariantVote, User
from app.routers import runtime
from app.utils.dependencies import get_current_active_user


class AsyncSessionAdapter:
    def __init__(self, session: Session):
        self.session = session

    async def execute(self, stmt):
        return self.session.execute(stmt)

    def add(self, obj) -> None:
        self.session.add(obj)

    async def commit(self) -> None:
        self.session.commit()

    async def rollback(self) -> None:
        self.session.rollback()


def _make_user() -> User:
    return User(
        id="user_edge_1",
        sync_id="sync_user_edge_1",
        username="EdgeTester",
        discriminator="4242",
        email="edge-tester@example.com",
        password_hash="hash",
    )


@pytest.mark.asyncio
async def test_edge_routes_serve_chooser_and_variant_pages(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    chooser = tmp_path / "edge_ui_chooser.html"
    chooser.write_text("<html><body>chooser</body></html>")
    original = tmp_path / "index.html"
    original.write_text("<html><body>original</body></html>")
    ui_b = tmp_path / "new_ui_b.html"
    ui_b.write_text("<html><body>ui-b</body></html>")

    from app import main

    monkeypatch.setattr(main.settings, "edge_mode_enabled", True)
    monkeypatch.setattr(main, "EDGE_CHOOSER_FILE", chooser)
    monkeypatch.setattr(main, "INDEX_FILE", original)
    monkeypatch.setattr(main, "NEW_UI_A_FILE", tmp_path / "new_ui_a.html")
    monkeypatch.setattr(main, "NEW_UI_B_FILE", ui_b)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        chooser_res = await client.get("/edge")
        original_res = await client.get("/edge/ui/original")
        ui_a_res = await client.get("/edge/ui/a")
        ui_b_res = await client.get("/edge/ui/b")

    assert chooser_res.status_code == 200
    assert "chooser" in chooser_res.text

    assert original_res.status_code == 200
    assert "original" in original_res.text

    assert ui_a_res.status_code == 200
    assert "not available yet" in ui_a_res.text

    assert ui_b_res.status_code == 200
    assert "ui-b" in ui_b_res.text


@pytest.mark.asyncio
async def test_runtime_ui_variants_catalog_and_vote_updates(tmp_path: Path) -> None:
    db_path = tmp_path / "ui_variants.sqlite3"
    variant_root = tmp_path / "variant-root"
    variant_root.mkdir()
    (variant_root / "index.html").write_text("<html><body>original</body></html>")
    (variant_root / "new_ui_b.html").write_text("<html><body>ui-b</body></html>")
    engine = create_engine(f"sqlite:///{db_path}")
    User.__table__.create(bind=engine)
    UiVariantVote.__table__.create(bind=engine)
    SessionLocal = sessionmaker(bind=engine)

    with SessionLocal() as session:
        session.add(_make_user())
        session.commit()

    async def override_get_db():
        session = SessionLocal()
        try:
            yield AsyncSessionAdapter(session)
        finally:
            session.close()

    async def override_current_user():
        return _make_user()

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_active_user] = override_current_user

    original_project_root = runtime.PROJECT_ROOT
    runtime.PROJECT_ROOT = variant_root

    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            stable_initial = await client.get("/api/v1/runtime/ui-variants")
            initial = await client.get("/edge/api/v1/runtime/ui-variants")
            first_vote = await client.put("/edge/api/v1/runtime/ui-variants/vote", json={"variant_key": "original"})
            second_vote = await client.put("/edge/api/v1/runtime/ui-variants/vote", json={"variant_key": "ui_b"})

        assert stable_initial.status_code == 200
        assert initial.status_code == 200
        initial_payload = initial.json()["data"]
        assert initial_payload["current_vote"] is None
        assert {item["key"]: item["available"] for item in initial_payload["variants"]} == {
            "original": True,
            "ui_a": False,
            "ui_b": True,
        }

        first_payload = first_vote.json()["data"]
        first_counts = {item["key"]: item["vote_count"] for item in first_payload["variants"]}
        assert first_payload["current_vote"] == "original"
        assert first_counts["original"] == 1
        assert first_counts["ui_b"] == 0

        second_payload = second_vote.json()["data"]
        second_counts = {item["key"]: item["vote_count"] for item in second_payload["variants"]}
        assert second_payload["current_vote"] == "ui_b"
        assert second_counts["original"] == 0
        assert second_counts["ui_b"] == 1
    finally:
        runtime.PROJECT_ROOT = original_project_root
        app.dependency_overrides.clear()
