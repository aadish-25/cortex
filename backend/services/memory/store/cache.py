from datetime import datetime, timezone
import json
import logging
from typing import List
import uuid

from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, or_, select

from backend.db.redis import redis_manager
from backend.models.memory import MemoryCategory, MemoryStatus, UserMemory

logger = logging.getLogger(__name__)

MEMORY_CACHE_TTL_SECONDS = 300


def _prefs_cache_key(user_id: uuid.UUID) -> str:
    return f"cortex:mem:prefs:{user_id}"


def _count_cache_key(user_id: uuid.UUID) -> str:
    return f"cortex:mem:count:{user_id}"


async def invalidate_user_memory_cache(user_id: uuid.UUID) -> None:
    client = await redis_manager.get_client()
    if client is None:
        return
    try:
        await client.delete(_prefs_cache_key(user_id), _count_cache_key(user_id))
    except Exception as exc:
        logger.warning("Failed to invalidate memory cache for user %s: %s", user_id, exc)


async def get_cached_active_preferences(
    session: AsyncSession,
    user_id: uuid.UUID,
    limit: int = 8,
) -> List[str]:
    """Returns active user communication/formatting preferences (Redis-cached, <3ms)."""
    redis_client = await redis_manager.get_client()
    cache_key = _prefs_cache_key(user_id)
    if redis_client is not None:
        try:
            cached = await redis_client.get(cache_key)
            if cached is not None:
                items = json.loads(cached)
                if isinstance(items, list):
                    return [str(x) for x in items[:limit]]
        except Exception as exc:
            logger.debug("Memory prefs cache read skipped: %s", exc)

    now = datetime.now(timezone.utc)
    stmt = (
        select(UserMemory.content)
        .where(
            UserMemory.user_id == user_id,
            UserMemory.category == MemoryCategory.PREFERENCE.value,
            UserMemory.status == MemoryStatus.ACTIVE.value,
            or_(UserMemory.expires_at == None, UserMemory.expires_at > now),  # noqa: E711
        )
        .order_by(col(UserMemory.updated_at).desc())
        .limit(max(limit, 8))
    )
    rows = (await session.execute(stmt)).scalars().all()
    prefs = [str(r) for r in rows if r]

    if redis_client is not None:
        try:
            await redis_client.set(cache_key, json.dumps(prefs), ex=MEMORY_CACHE_TTL_SECONDS)
        except Exception as exc:
            logger.debug("Memory prefs cache write skipped: %s", exc)

    return prefs[:limit]


async def get_cached_active_non_pref_count(
    session: AsyncSession,
    user_id: uuid.UUID,
) -> int:
    """Returns count of active non-preference memories for <1ms fast-exit gating."""
    redis_client = await redis_manager.get_client()
    cache_key = _count_cache_key(user_id)
    if redis_client is not None:
        try:
            cached = await redis_client.get(cache_key)
            if cached is not None:
                return int(cached)
        except Exception as exc:
            logger.debug("Memory count cache read skipped: %s", exc)

    now = datetime.now(timezone.utc)
    stmt = select(func.count(UserMemory.id)).where(
        UserMemory.user_id == user_id,
        UserMemory.status == MemoryStatus.ACTIVE.value,
        UserMemory.category != MemoryCategory.PREFERENCE.value,
        or_(UserMemory.expires_at == None, UserMemory.expires_at > now),  # noqa: E711
    )
    count = int((await session.execute(stmt)).scalar_one() or 0)

    if redis_client is not None:
        try:
            await redis_client.set(cache_key, str(count), ex=MEMORY_CACHE_TTL_SECONDS)
        except Exception as exc:
            logger.debug("Memory count cache write skipped: %s", exc)

    return count
