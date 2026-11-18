import asyncio
from datetime import datetime, timezone
import logging
from typing import Any, List, Optional, Sequence
import uuid

from qdrant_client import QdrantClient, models
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from backend.models.memory import (
    MemorySourceType,
    MemoryStatus,
    MemorySyncStatus,
    UserMemory,
    normalize_memory_category,
)
from backend.services.memory.store.cache import invalidate_user_memory_cache
from backend.services.vector.embedding_service import generate_dense_embeddings

logger = logging.getLogger(__name__)


def resolve_qdrant_client(client: Optional[QdrantClient]) -> Optional[QdrantClient]:
    if client is not None:
        return client
    try:
        from backend.db.qdrant import get_qdrant_client
        return get_qdrant_client()
    except Exception as exc:
        logger.warning("Qdrant unavailable for memory sync: %s", exc)
        return None


async def upsert_qdrant_memory_point(
    client: Optional[QdrantClient],
    collection_name: str,
    memory: UserMemory,
) -> bool:
    qclient = resolve_qdrant_client(client)
    if qclient is None:
        return False
    try:
        embeddings = await asyncio.to_thread(generate_dense_embeddings, [memory.content])
        if not embeddings:
            return False
        payload = {
            "user_id": str(memory.user_id),
            "fact": memory.content,
            "content": memory.content,
            "category": memory.category,
            "status": memory.status,
            "created_at": memory.created_at.isoformat(),
            "updated_at": memory.updated_at.isoformat(),
        }
        point = models.PointStruct(id=str(memory.id), vector=embeddings[0], payload=payload)
        await asyncio.to_thread(qclient.upsert, collection_name=collection_name, points=[point])
        return True
    except Exception as exc:
        logger.warning("Qdrant upsert failed for memory %s (will retry in sweeper): %s", memory.id, exc)
        return False


async def delete_qdrant_memory_points(
    client: Optional[QdrantClient],
    collection_name: str,
    point_ids: Sequence[str],
) -> bool:
    if not point_ids:
        return True
    qclient = resolve_qdrant_client(client)
    if qclient is None:
        return False
    try:
        await asyncio.to_thread(
            qclient.delete,
            collection_name=collection_name,
            points_selector=models.PointIdsList(points=list(point_ids)),
        )
        return True
    except Exception as exc:
        logger.warning("Qdrant delete failed for %d memory points (will retry in sweeper): %s", len(point_ids), exc)
        return False


async def query_qdrant_memory_points(
    client: Optional[QdrantClient],
    collection_name: str,
    user_id: uuid.UUID,
    query: str,
    *,
    limit: int = 5,
    threshold: float = 0.52,
    categories: Optional[Sequence[str]] = None,
) -> List[Any]:
    qclient = resolve_qdrant_client(client)
    if qclient is None or not query or not query.strip():
        return []
    try:
        embeddings = await asyncio.to_thread(generate_dense_embeddings, [query.strip()])
        if not embeddings:
            return []
        must_conditions: List[Any] = [
            models.FieldCondition(key="user_id", match=models.MatchValue(value=str(user_id))),
        ]
        if categories:
            norm_cats = [normalize_memory_category(c) for c in categories]
            if len(norm_cats) == 1:
                must_conditions.append(
                    models.FieldCondition(key="category", match=models.MatchValue(value=norm_cats[0]))
                )
            else:
                must_conditions.append(
                    models.FieldCondition(key="category", match=models.MatchAny(any=norm_cats))
                )
        results = await asyncio.to_thread(
            qclient.query_points,
            collection_name=collection_name,
            query=embeddings[0],
            query_filter=models.Filter(must=must_conditions),
            limit=max(limit * 2, 10),
            score_threshold=threshold,
        )
        return list(results.points if hasattr(results, "points") else results)
    except Exception as exc:
        logger.warning("Qdrant semantic memory search failed (%s); falling back safely", exc)
        return []


async def backfill_qdrant_memories_to_pg(
    session: AsyncSession,
    client: Optional[QdrantClient],
    collection_name: str,
    user_id: uuid.UUID,
) -> int:
    """Idempotent import of legacy Qdrant-only memories into PostgreSQL."""
    qclient = resolve_qdrant_client(client)
    if qclient is None:
        return 0
    try:
        scroll_res = await asyncio.to_thread(
            qclient.scroll,
            collection_name=collection_name,
            scroll_filter=models.Filter(
                must=[models.FieldCondition(key="user_id", match=models.MatchValue(value=str(user_id)))]
            ),
            limit=200,
            with_payload=True,
            with_vectors=False,
        )
        points = scroll_res[0] if scroll_res else []
    except Exception as exc:
        logger.warning("Qdrant backfill scroll failed for user %s: %s", user_id, exc)
        return 0

    if not points:
        return 0

    existing_ids = set(
        (await session.execute(select(UserMemory.id).where(UserMemory.user_id == user_id))).scalars().all()
    )
    imported = 0
    now = datetime.now(timezone.utc)
    for point in points:
        payload = point.payload or {}
        fact = (payload.get("content") or payload.get("fact") or "").strip()
        if not fact:
            continue
        try:
            pid = uuid.UUID(str(point.id))
        except ValueError:
            continue
        if pid in existing_ids:
            continue
        created_raw = payload.get("created_at")
        try:
            created_at = datetime.fromisoformat(created_raw) if created_raw else now
        except Exception:
            created_at = now
        session.add(
            UserMemory(
                id=pid,
                user_id=user_id,
                content=fact,
                category=normalize_memory_category(payload.get("category")),
                status=MemoryStatus.ACTIVE.value,
                confidence=1.0,
                source_type=MemorySourceType.BACKFILLED.value,
                version=1,
                sync_status=MemorySyncStatus.SYNCED.value,
                created_at=created_at,
                updated_at=created_at,
            )
        )
        imported += 1

    if imported:
        await session.commit()
        await invalidate_user_memory_cache(user_id)
        logger.info("Backfilled %d legacy Qdrant memories into PostgreSQL for user %s", imported, user_id)
    return imported
