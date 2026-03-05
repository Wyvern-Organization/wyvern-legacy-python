import asyncio

import pytest

from app.services.presence import presence_service


@pytest.mark.asyncio
async def test_presence_service_smoke() -> None:
    # Integration tests for Redis-backed services should run with an active Redis instance.
    # This smoke test is intentionally lightweight and can be adapted in CI.
    _ = presence_service
    await asyncio.sleep(0)
