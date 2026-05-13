from __future__ import annotations

import asyncio
import hashlib
import logging
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Iterable

from sqlalchemy import and_, delete, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import AsyncSessionLocal
from app.models import (
    Channel,
    RecommendationEmbedding,
    RecommendationSignal,
    Server,
    ServerActivityLog,
    ServerMember,
    User,
    UserRecommendation,
)


TARGET_SERVER = "server"
TARGET_USER = "user"
PUBLIC_TARGET_TYPES = {TARGET_SERVER, TARGET_USER}
MODEL_UNAVAILABLE_MESSAGE = "sentence-transformers is required when RECOMMENDATIONS_ENABLED=true and embeddings are refreshed"

logger = logging.getLogger(__name__)
_recommendation_worker_task: asyncio.Task | None = None
_last_full_refresh_at: datetime | None = None


@dataclass(frozen=True)
class RankedRecommendation:
    item: Any
    score: float
    reason: str
    factors: dict[str, float]


class RecommendationEmbedder:
    def __init__(self) -> None:
        self._model: Any | None = None
        self._failed_model_name: str | None = None

    def _load_model(self, model_name: str) -> Any | None:
        if self._model is not None:
            return self._model
        if self._failed_model_name == model_name:
            return None
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore
        except ImportError:
            self._failed_model_name = model_name
            logger.warning(MODEL_UNAVAILABLE_MESSAGE)
            return None

        self._model = SentenceTransformer(model_name)
        return self._model

    async def embed_text(self, text: str, model_name: str) -> list[float] | None:
        normalized = " ".join((text or "").split())
        if not normalized:
            return None
        model = self._load_model(model_name)
        if model is None:
            return None
        return await asyncio.to_thread(self._embed_sync, model, normalized)

    @staticmethod
    def _embed_sync(model: Any, text: str) -> list[float]:
        embedding = model.encode(text, normalize_embeddings=True)
        if hasattr(embedding, "tolist"):
            embedding = embedding.tolist()
        return [float(value) for value in embedding]


embedder = RecommendationEmbedder()


def recommendations_enabled() -> bool:
    return bool(get_settings().recommendations_enabled)


def now_utc() -> datetime:
    return datetime.now(tz=UTC)


def source_hash(source_text: str) -> str:
    return hashlib.sha256(" ".join(source_text.split()).encode("utf-8")).hexdigest()


def build_user_embedding_source(user: Any) -> str | None:
    if not bool(getattr(user, "directory_opt_in", False)):
        return None
    parts = [
        str(getattr(user, "display_name", "") or ""),
        str(getattr(user, "username", "") or ""),
        str(getattr(user, "bio", "") or ""),
    ]
    text = " ".join(part.strip() for part in parts if part and part.strip())
    return text or None


def build_server_embedding_source(server: Any, channel_names: Iterable[str] | None = None) -> str | None:
    if not bool(getattr(server, "directory_opt_in", False)):
        return None
    parts = [
        str(getattr(server, "name", "") or ""),
        str(getattr(server, "description", "") or ""),
    ]
    for name in channel_names or []:
        if name:
            parts.append(str(name))
    text = " ".join(part.strip() for part in parts if part and part.strip())
    return text or None


def normalize_embedding(value: Any) -> list[float] | None:
    if not isinstance(value, list) or not value:
        return None
    normalized: list[float] = []
    for item in value:
        try:
            normalized.append(float(item))
        except (TypeError, ValueError):
            return None
    norm = math.sqrt(sum(item * item for item in normalized))
    if norm <= 0:
        return None
    return normalized


def cosine_similarity(left: list[float] | None, right: list[float] | None) -> float:
    left = normalize_embedding(left)
    right = normalize_embedding(right)
    if left is None or right is None or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm <= 0 or right_norm <= 0:
        return 0.0
    return max(0.0, dot / (left_norm * right_norm))


def average_embeddings(embeddings: Iterable[list[float] | None]) -> list[float] | None:
    usable = [normalize_embedding(item) for item in embeddings]
    usable = [item for item in usable if item is not None]
    if not usable:
        return None
    width = len(usable[0])
    same_width = [item for item in usable if len(item) == width]
    if not same_width:
        return None
    averaged = [sum(item[index] for item in same_width) / len(same_width) for index in range(width)]
    return normalize_embedding(averaged)


def freshness_score(created_at: datetime | None, current_time: datetime | None = None) -> float:
    if created_at is None:
        return 0.0
    current_time = current_time or now_utc()
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    age_days = max(0.0, (current_time - created_at).total_seconds() / 86400)
    return max(0.0, 1.0 - min(age_days, 30.0) / 30.0)


def signal_score(raw_value: float | int | None) -> float:
    try:
        value = float(raw_value or 0)
    except (TypeError, ValueError):
        value = 0.0
    return min(max(value, 0.0), 5.0) / 5.0


def recommendation_reason(target_type: str, factors: dict[str, float]) -> str:
    if factors.get("semantic", 0.0) >= 0.35:
        return "Similar interests"
    if target_type == TARGET_USER and factors.get("mutual", 0.0) > 0:
        return "Shared servers"
    if target_type == TARGET_SERVER and factors.get("activity", 0.0) >= 0.2:
        return "Active public community"
    if factors.get("signal", 0.0) > 0:
        return "Recent activity"
    return "Suggested"


def rank_user_candidates(
    *,
    current_user_id: str,
    candidates: Iterable[Any],
    interest_embedding: list[float] | None,
    candidate_embeddings: dict[str, list[float] | None],
    mutual_counts: dict[str, int] | None = None,
    signal_scores: dict[str, float] | None = None,
    max_candidates: int = 50,
    current_time: datetime | None = None,
) -> list[RankedRecommendation]:
    mutual_counts = mutual_counts or {}
    signal_scores = signal_scores or {}
    ranked: list[RankedRecommendation] = []
    for user in candidates:
        user_id = str(getattr(user, "id", ""))
        if not user_id or user_id == current_user_id or not bool(getattr(user, "directory_opt_in", False)):
            continue
        semantic = cosine_similarity(interest_embedding, candidate_embeddings.get(user_id))
        mutual = min(float(mutual_counts.get(user_id, 0)), 3.0) / 3.0
        signal = signal_score(signal_scores.get(user_id, 0.0))
        fresh = freshness_score(getattr(user, "created_at", None), current_time)
        factors = {
            "semantic": semantic,
            "mutual": mutual,
            "signal": signal,
            "freshness": fresh,
        }
        score = (semantic * 0.65) + (mutual * 0.18) + (signal * 0.12) + (fresh * 0.05)
        ranked.append(
            RankedRecommendation(
                item=user,
                score=round(score, 6),
                reason=recommendation_reason(TARGET_USER, factors),
                factors={key: round(value, 6) for key, value in factors.items()},
            )
        )
    return sorted(ranked, key=lambda item: (-item.score, str(getattr(item.item, "id", ""))))[:max_candidates]


def rank_server_candidates(
    *,
    candidates: Iterable[Any],
    joined_server_ids: set[str] | None,
    interest_embedding: list[float] | None,
    candidate_embeddings: dict[str, list[float] | None],
    member_counts: dict[str, int] | None = None,
    activity_counts: dict[str, int] | None = None,
    signal_scores: dict[str, float] | None = None,
    max_candidates: int = 50,
    current_time: datetime | None = None,
) -> list[RankedRecommendation]:
    joined_server_ids = joined_server_ids or set()
    member_counts = member_counts or {}
    activity_counts = activity_counts or {}
    signal_scores = signal_scores or {}
    ranked: list[RankedRecommendation] = []
    for server in candidates:
        server_id = str(getattr(server, "id", ""))
        if not server_id or server_id in joined_server_ids or not bool(getattr(server, "directory_opt_in", False)):
            continue
        semantic = cosine_similarity(interest_embedding, candidate_embeddings.get(server_id))
        signal = signal_score(signal_scores.get(server_id, 0.0))
        activity = min(float(activity_counts.get(server_id, 0)), 20.0) / 20.0
        popularity = min(float(member_counts.get(server_id, 0)), 50.0) / 50.0
        fresh = freshness_score(getattr(server, "created_at", None), current_time)
        factors = {
            "semantic": semantic,
            "signal": signal,
            "activity": activity,
            "popularity": popularity,
            "freshness": fresh,
        }
        score = (semantic * 0.60) + (signal * 0.15) + (activity * 0.10) + (popularity * 0.10) + (fresh * 0.05)
        ranked.append(
            RankedRecommendation(
                item=server,
                score=round(score, 6),
                reason=recommendation_reason(TARGET_SERVER, factors),
                factors={key: round(value, 6) for key, value in factors.items()},
            )
        )
    return sorted(ranked, key=lambda item: (-item.score, str(getattr(item.item, "id", ""))))[:max_candidates]


def refresh_due(
    *,
    refreshed_at: datetime | None,
    last_activity_at: datetime | None,
    current_time: datetime,
    active_hours: int,
    normal_hours: int,
) -> bool:
    if refreshed_at is None:
        return True
    if refreshed_at.tzinfo is None:
        refreshed_at = refreshed_at.replace(tzinfo=UTC)
    if last_activity_at and last_activity_at.tzinfo is None:
        last_activity_at = last_activity_at.replace(tzinfo=UTC)
    active_since = current_time - timedelta(hours=active_hours)
    active = last_activity_at is not None and last_activity_at >= active_since
    window_hours = active_hours if active else normal_hours
    return refreshed_at <= current_time - timedelta(hours=window_hours)


async def _delete_entity_state(db: AsyncSession, entity_type: str, entity_id: str) -> None:
    await db.execute(
        delete(RecommendationEmbedding).where(
            and_(RecommendationEmbedding.entity_type == entity_type, RecommendationEmbedding.entity_id == entity_id)
        )
    )
    await db.execute(
        delete(UserRecommendation).where(
            and_(UserRecommendation.target_type == entity_type, UserRecommendation.target_id == entity_id)
        )
    )


async def _channel_names_for_server(db: AsyncSession, server_id: str) -> list[str]:
    result = await db.execute(select(Channel.name).where(Channel.server_id == server_id).order_by(Channel.position.asc(), Channel.id.asc()))
    return [str(name) for name in result.scalars().all() if name]


async def _public_source_for_entity(db: AsyncSession, entity_type: str, entity_id: str) -> str | None:
    if entity_type == TARGET_USER:
        user = await db.get(User, entity_id)
        return build_user_embedding_source(user) if user is not None else None
    if entity_type == TARGET_SERVER:
        server = await db.get(Server, entity_id)
        if server is None:
            return None
        return build_server_embedding_source(server, await _channel_names_for_server(db, entity_id))
    return None


async def mark_public_entity_stale(db: AsyncSession, entity_type: str, entity_id: str) -> None:
    if not recommendations_enabled() or entity_type not in PUBLIC_TARGET_TYPES:
        return
    source = await _public_source_for_entity(db, entity_type, entity_id)
    if source is None:
        await _delete_entity_state(db, entity_type, entity_id)
        return
    model_name = get_settings().recommendation_embedding_model
    digest = source_hash(source)
    result = await db.execute(
        select(RecommendationEmbedding).where(
            RecommendationEmbedding.entity_type == entity_type,
            RecommendationEmbedding.entity_id == entity_id,
            RecommendationEmbedding.embedding_model == model_name,
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        db.add(
            RecommendationEmbedding(
                entity_type=entity_type,
                entity_id=entity_id,
                embedding_model=model_name,
                source_hash=digest,
                stale=True,
                updated_at=now_utc(),
            )
        )
        return
    row.source_hash = digest
    row.stale = True
    row.updated_at = now_utc()


async def refresh_public_entity_embedding(entity_type: str, entity_id: str, db: AsyncSession | None = None) -> bool:
    if not recommendations_enabled() or entity_type not in PUBLIC_TARGET_TYPES:
        return False
    close_session = db is None
    if db is None:
        db = AsyncSessionLocal()
    try:
        source = await _public_source_for_entity(db, entity_type, entity_id)
        if source is None:
            await _delete_entity_state(db, entity_type, entity_id)
            if close_session:
                await db.commit()
            return False

        model_name = get_settings().recommendation_embedding_model
        digest = source_hash(source)
        result = await db.execute(
            select(RecommendationEmbedding).where(
                RecommendationEmbedding.entity_type == entity_type,
                RecommendationEmbedding.entity_id == entity_id,
                RecommendationEmbedding.embedding_model == model_name,
            )
        )
        row = result.scalar_one_or_none()
        if row is not None and not row.stale and row.source_hash == digest and row.embedding:
            return False

        embedding = await embedder.embed_text(source, model_name)
        current_time = now_utc()
        if row is None:
            row = RecommendationEmbedding(
                entity_type=entity_type,
                entity_id=entity_id,
                embedding_model=model_name,
                source_hash=digest,
                embedding=embedding,
                stale=embedding is None,
                embedded_at=current_time if embedding is not None else None,
                updated_at=current_time,
            )
            db.add(row)
        else:
            row.source_hash = digest
            row.embedding = embedding
            row.stale = embedding is None
            row.embedded_at = current_time if embedding is not None else row.embedded_at
            row.updated_at = current_time

        if close_session:
            await db.commit()
        return embedding is not None
    finally:
        if close_session:
            await db.close()


async def record_recommendation_signal(
    db: AsyncSession,
    user_id: str,
    target_type: str,
    target_id: str,
    action: str,
    *,
    weight: float = 1.0,
) -> None:
    if not recommendations_enabled() or target_type not in PUBLIC_TARGET_TYPES:
        return
    if target_type == TARGET_USER:
        target = await db.get(User, target_id)
        if target is None or not target.directory_opt_in or target.id == user_id:
            return
    elif target_type == TARGET_SERVER:
        target = await db.get(Server, target_id)
        if target is None or not target.directory_opt_in:
            return

    result = await db.execute(
        select(RecommendationSignal).where(
            RecommendationSignal.user_id == user_id,
            RecommendationSignal.target_type == target_type,
            RecommendationSignal.target_id == target_id,
            RecommendationSignal.action == action,
        )
    )
    current_time = now_utc()
    signal = result.scalar_one_or_none()
    if signal is None:
        db.add(
            RecommendationSignal(
                user_id=user_id,
                target_type=target_type,
                target_id=target_id,
                action=action,
                count=1,
                weight=weight,
                last_seen_at=current_time,
                updated_at=current_time,
            )
        )
        return
    signal.count += 1
    signal.weight = weight
    signal.last_seen_at = current_time
    signal.updated_at = current_time


async def _embedding_map(db: AsyncSession, entity_type: str, entity_ids: Iterable[str]) -> dict[str, list[float] | None]:
    ids = [entity_id for entity_id in set(entity_ids) if entity_id]
    if not ids:
        return {}
    model_name = get_settings().recommendation_embedding_model
    result = await db.execute(
        select(RecommendationEmbedding.entity_id, RecommendationEmbedding.embedding).where(
            RecommendationEmbedding.entity_type == entity_type,
            RecommendationEmbedding.entity_id.in_(ids),
            RecommendationEmbedding.embedding_model == model_name,
            RecommendationEmbedding.stale.is_(False),
        )
    )
    return {str(entity_id): normalize_embedding(embedding) for entity_id, embedding in result.all()}


async def _current_user_interest_embedding(db: AsyncSession, current_user: User) -> list[float] | None:
    model_name = get_settings().recommendation_embedding_model
    server_result = await db.execute(
        select(RecommendationEmbedding.embedding)
        .join(Server, and_(Server.id == RecommendationEmbedding.entity_id, RecommendationEmbedding.entity_type == TARGET_SERVER))
        .join(ServerMember, ServerMember.server_id == Server.id)
        .where(
            ServerMember.user_id == current_user.id,
            Server.directory_opt_in.is_(True),
            RecommendationEmbedding.embedding_model == model_name,
            RecommendationEmbedding.stale.is_(False),
        )
    )
    embeddings = [normalize_embedding(item) for item in server_result.scalars().all()]
    if current_user.directory_opt_in:
        user_result = await db.execute(
            select(RecommendationEmbedding.embedding).where(
                RecommendationEmbedding.entity_type == TARGET_USER,
                RecommendationEmbedding.entity_id == current_user.id,
                RecommendationEmbedding.embedding_model == model_name,
                RecommendationEmbedding.stale.is_(False),
            )
        )
        embeddings.append(normalize_embedding(user_result.scalar_one_or_none()))
    return average_embeddings(embeddings)


async def _signal_scores(db: AsyncSession, user_id: str, target_type: str, target_ids: Iterable[str]) -> dict[str, float]:
    ids = [target_id for target_id in set(target_ids) if target_id]
    if not ids:
        return {}
    result = await db.execute(
        select(
            RecommendationSignal.target_id,
            func.sum(RecommendationSignal.count * RecommendationSignal.weight).label("score"),
        )
        .where(
            RecommendationSignal.user_id == user_id,
            RecommendationSignal.target_type == target_type,
            RecommendationSignal.target_id.in_(ids),
        )
        .group_by(RecommendationSignal.target_id)
    )
    return {str(target_id): float(score or 0.0) for target_id, score in result.all()}


async def _mutual_public_server_counts(db: AsyncSession, current_user_id: str, candidate_user_ids: Iterable[str]) -> dict[str, int]:
    candidate_ids = [user_id for user_id in set(candidate_user_ids) if user_id and user_id != current_user_id]
    if not candidate_ids:
        return {}
    public_servers = (
        select(ServerMember.server_id)
        .join(Server, Server.id == ServerMember.server_id)
        .where(ServerMember.user_id == current_user_id, Server.directory_opt_in.is_(True))
        .subquery()
    )
    result = await db.execute(
        select(ServerMember.user_id, func.count(ServerMember.server_id).label("mutual_count"))
        .join(public_servers, public_servers.c.server_id == ServerMember.server_id)
        .where(ServerMember.user_id.in_(candidate_ids))
        .group_by(ServerMember.user_id)
    )
    return {str(user_id): int(count or 0) for user_id, count in result.all()}


async def _server_member_counts(db: AsyncSession, server_ids: Iterable[str]) -> dict[str, int]:
    ids = [server_id for server_id in set(server_ids) if server_id]
    if not ids:
        return {}
    result = await db.execute(
        select(ServerMember.server_id, func.count(ServerMember.user_id).label("member_count"))
        .where(ServerMember.server_id.in_(ids))
        .group_by(ServerMember.server_id)
    )
    return {str(server_id): int(count or 0) for server_id, count in result.all()}


async def _server_activity_counts(db: AsyncSession, server_ids: Iterable[str]) -> dict[str, int]:
    ids = [server_id for server_id in set(server_ids) if server_id]
    if not ids:
        return {}
    result = await db.execute(
        select(ServerActivityLog.server_id, func.count(ServerActivityLog.id).label("activity_count"))
        .where(ServerActivityLog.server_id.in_(ids))
        .group_by(ServerActivityLog.server_id)
    )
    return {str(server_id): int(count or 0) for server_id, count in result.all()}


async def _joined_server_ids(db: AsyncSession, user_id: str) -> set[str]:
    result = await db.execute(select(ServerMember.server_id).where(ServerMember.user_id == user_id))
    return {str(server_id) for server_id in result.scalars().all()}


async def recommended_user_rankings(db: AsyncSession, current_user: User) -> list[RankedRecommendation]:
    settings = get_settings()
    result = await db.execute(
        select(User)
        .where(User.directory_opt_in.is_(True), User.id != current_user.id)
        .order_by(func.lower(func.coalesce(User.display_name, User.username)).asc(), User.id.asc())
    )
    candidates = result.scalars().all()
    candidate_ids = [user.id for user in candidates]
    return rank_user_candidates(
        current_user_id=current_user.id,
        candidates=candidates,
        interest_embedding=await _current_user_interest_embedding(db, current_user),
        candidate_embeddings=await _embedding_map(db, TARGET_USER, candidate_ids),
        mutual_counts=await _mutual_public_server_counts(db, current_user.id, candidate_ids),
        signal_scores=await _signal_scores(db, current_user.id, TARGET_USER, candidate_ids),
        max_candidates=settings.recommendation_max_candidates,
    )


async def recommended_server_rankings(db: AsyncSession, current_user: User) -> list[RankedRecommendation]:
    settings = get_settings()
    result = await db.execute(
        select(Server)
        .where(Server.directory_opt_in.is_(True))
        .order_by(func.lower(Server.name).asc(), Server.id.asc())
    )
    candidates = result.scalars().all()
    candidate_ids = [server.id for server in candidates]
    joined_ids = await _joined_server_ids(db, current_user.id)
    return rank_server_candidates(
        candidates=candidates,
        joined_server_ids=joined_ids,
        interest_embedding=await _current_user_interest_embedding(db, current_user),
        candidate_embeddings=await _embedding_map(db, TARGET_SERVER, candidate_ids),
        member_counts=await _server_member_counts(db, candidate_ids),
        activity_counts=await _server_activity_counts(db, candidate_ids),
        signal_scores=await _signal_scores(db, current_user.id, TARGET_SERVER, candidate_ids),
        max_candidates=settings.recommendation_max_candidates,
    )


async def _replace_cached_recommendations(
    db: AsyncSession,
    user_id: str,
    target_type: str,
    rankings: list[RankedRecommendation],
) -> None:
    await db.execute(delete(UserRecommendation).where(UserRecommendation.user_id == user_id, UserRecommendation.target_type == target_type))
    current_time = now_utc()
    for index, ranking in enumerate(rankings, start=1):
        db.add(
            UserRecommendation(
                user_id=user_id,
                target_type=target_type,
                target_id=str(getattr(ranking.item, "id")),
                score=ranking.score,
                reason=ranking.reason,
                factors=ranking.factors,
                rank=index,
                refreshed_at=current_time,
            )
        )


async def refresh_user_recommendations(user_id: str, db: AsyncSession | None = None) -> bool:
    if not recommendations_enabled():
        return False
    close_session = db is None
    if db is None:
        db = AsyncSessionLocal()
    try:
        user = await db.get(User, user_id)
        if user is None:
            return False
        user_rankings = await recommended_user_rankings(db, user)
        server_rankings = await recommended_server_rankings(db, user)
        await _replace_cached_recommendations(db, user.id, TARGET_USER, user_rankings)
        await _replace_cached_recommendations(db, user.id, TARGET_SERVER, server_rankings)
        if close_session:
            await db.commit()
        return True
    finally:
        if close_session:
            await db.close()


async def cached_rankings(db: AsyncSession, user_id: str, target_type: str) -> list[UserRecommendation]:
    result = await db.execute(
        select(UserRecommendation)
        .where(UserRecommendation.user_id == user_id, UserRecommendation.target_type == target_type)
        .order_by(UserRecommendation.rank.asc(), desc(UserRecommendation.score), UserRecommendation.target_id.asc())
        .limit(get_settings().recommendation_max_candidates)
    )
    return result.scalars().all()


async def refresh_recommendation_batch(mode: str = "stale") -> int:
    if not recommendations_enabled():
        return 0
    refreshed = 0
    async with AsyncSessionLocal() as db:
        settings = get_settings()
        model_name = settings.recommendation_embedding_model
        if mode == "full":
            public_users_result = await db.execute(select(User.id).where(User.directory_opt_in.is_(True)).limit(100))
            for user_id in public_users_result.scalars().all():
                if await refresh_public_entity_embedding(TARGET_USER, str(user_id), db):
                    refreshed += 1
            public_servers_result = await db.execute(select(Server.id).where(Server.directory_opt_in.is_(True)).limit(100))
            for server_id in public_servers_result.scalars().all():
                if await refresh_public_entity_embedding(TARGET_SERVER, str(server_id), db):
                    refreshed += 1

        stale_result = await db.execute(
            select(RecommendationEmbedding)
            .where(RecommendationEmbedding.embedding_model == model_name, RecommendationEmbedding.stale.is_(True))
            .limit(100)
        )
        for row in stale_result.scalars().all():
            if await refresh_public_entity_embedding(row.entity_type, row.entity_id, db):
                refreshed += 1

        if mode in {"active", "normal", "full"}:
            users_result = await db.execute(select(User.id).order_by(User.created_at.asc()))
            user_ids = [str(user_id) for user_id in users_result.scalars().all()]
            if mode != "full":
                cutoff_hours = settings.recommendation_active_refresh_hours if mode == "active" else settings.recommendation_normal_refresh_hours
                cutoff = now_utc() - timedelta(hours=cutoff_hours)
                due_result = await db.execute(
                    select(UserRecommendation.user_id)
                    .where(UserRecommendation.refreshed_at <= cutoff)
                    .group_by(UserRecommendation.user_id)
                )
                due_ids = {str(user_id) for user_id in due_result.scalars().all()}
                user_ids = [user_id for user_id in user_ids if user_id in due_ids]
            for user_id in user_ids[:100]:
                if await refresh_user_recommendations(user_id, db):
                    refreshed += 1

        await db.commit()
    return refreshed


async def _recommendation_worker() -> None:
    global _last_full_refresh_at
    settings = get_settings()
    while True:
        try:
            await refresh_recommendation_batch("stale")
            current_time = now_utc()
            if _last_full_refresh_at is None or _last_full_refresh_at <= current_time - timedelta(hours=settings.recommendation_full_refresh_hours):
                await refresh_recommendation_batch("full")
                _last_full_refresh_at = current_time
            else:
                await refresh_recommendation_batch("active")
            await asyncio.sleep(settings.recommendation_worker_poll_seconds)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Recommendation worker pass failed")
            await asyncio.sleep(settings.recommendation_worker_poll_seconds)


async def start_recommendation_worker() -> None:
    global _recommendation_worker_task
    if not recommendations_enabled():
        logger.info("Recommendation worker disabled")
        return
    if _recommendation_worker_task is None or _recommendation_worker_task.done():
        _recommendation_worker_task = asyncio.create_task(_recommendation_worker())


async def stop_recommendation_worker() -> None:
    global _recommendation_worker_task
    if _recommendation_worker_task is None:
        return
    _recommendation_worker_task.cancel()
    try:
        await _recommendation_worker_task
    except asyncio.CancelledError:
        pass
    finally:
        _recommendation_worker_task = None
