from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import UiVariantVote


EDGE_UI_VARIANT_POLL_KEY = "edge_ui_v1"
EDGE_UI_VARIANT_LABELS: dict[str, str] = {
    "original": "Original",
    "ui_a": "UI A",
    "ui_b": "UI B",
}
EDGE_UI_VARIANT_ROUTES: dict[str, str] = {
    "original": "/edge/ui/original",
    "ui_a": "/edge/ui/a",
    "ui_b": "/edge/ui/b",
}


def is_valid_ui_variant_key(value: str) -> bool:
    return value in EDGE_UI_VARIANT_LABELS


def edge_ui_variant_files(project_root: Path) -> dict[str, Path]:
    return {
        "original": project_root / "index.html",
        "ui_a": project_root / "new_ui_a.html",
        "ui_b": project_root / "new_ui_b.html",
    }


def edge_ui_variant_availability(project_root: Path) -> dict[str, bool]:
    files = edge_ui_variant_files(project_root)
    return {key: path.exists() for key, path in files.items()}


async def get_ui_variant_vote_counts(db: AsyncSession, poll_key: str = EDGE_UI_VARIANT_POLL_KEY) -> dict[str, int]:
    result = await db.execute(
        select(UiVariantVote.variant_key, func.count(UiVariantVote.id))
        .where(UiVariantVote.poll_key == poll_key)
        .group_by(UiVariantVote.variant_key)
    )
    counts = {str(variant_key): int(count or 0) for variant_key, count in result.all()}
    for key in EDGE_UI_VARIANT_LABELS:
        counts.setdefault(key, 0)
    return counts


async def get_user_ui_variant_vote(
    db: AsyncSession,
    user_id: str,
    poll_key: str = EDGE_UI_VARIANT_POLL_KEY,
) -> UiVariantVote | None:
    result = await db.execute(
        select(UiVariantVote).where(
            UiVariantVote.user_id == str(user_id),
            UiVariantVote.poll_key == poll_key,
        )
    )
    return result.scalar_one_or_none()


async def upsert_user_ui_variant_vote(
    db: AsyncSession,
    user_id: str,
    variant_key: str,
    poll_key: str = EDGE_UI_VARIANT_POLL_KEY,
) -> UiVariantVote:
    vote = await get_user_ui_variant_vote(db, user_id, poll_key=poll_key)
    now = datetime.now(tz=UTC)

    if vote is None:
        vote = UiVariantVote(
            user_id=str(user_id),
            poll_key=poll_key,
            variant_key=variant_key,
        )
        db.add(vote)
    else:
        vote.variant_key = variant_key
        vote.updated_at = now

    await db.commit()
    return vote
