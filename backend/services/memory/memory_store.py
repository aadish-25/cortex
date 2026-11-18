import asyncio
from datetime import datetime, timezone
import json
import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple
import uuid

from qdrant_client import QdrantClient, models
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, or_, select

from backend.core.config import settings
from backend.db.redis import redis_manager
from backend.models.memory import (
    MemoryCategory,
    MemorySourceType,
    MemoryStatus,
    MemorySyncStatus,
    UserMemory,
    normalize_memory_category,
)
from backend.services.vector.embedding_service import generate_dense_embeddings

logger = logging.getLogger(__name__)

MEMORY_CACHE_TTL_SECONDS = 300


class MemoryVersionConflictError(Exception):
    """Raised when a concurrent mutation detects a stale memory version."""


def _prefs_cache_key(user_id: uuid.UUID) -> str:
    return f"cortex:mem:prefs:{user_id}"


def _count_cache_key(user_id: uuid.UUID) -> str:
    return f"cortex:mem:count:{user_id}"


class MemoryStore:
    """
    Canonical Memory Persistence Layer.
    - PostgreSQL (`user_memories`) is the authoritative source of truth.
    - Qdrant (`cortex_user_memories`) is the semantic vector index (same UUID per memory).
    - Redis caches per-user active preferences and active non-preference counts (invalidated on every write).
    """

    def __init__(self, collection_name: Optional[str] = None) -> None:
        self.collection_name = collection_name or settings.QDRANT_USER_MEMORIES_COLLECTION

    # ------------------------------------------------------------------
    # Redis Cache Helpers
    # ------------------------------------------------------------------

    async def invalidate_user_cache(self, user_id: uuid.UUID) -> None:
        client = await redis_manager.get_client()
        if client is None:
            return
        try:
            await client.delete(_prefs_cache_key(user_id), _count_cache_key(user_id))
        except Exception as exc:
            logger.warning("Failed to invalidate memory cache for user %s: %s", user_id, exc)

    async def get_active_preferences(
        self,
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

    async def get_active_non_pref_count(
        self,
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

    # ------------------------------------------------------------------
    # Qdrant Sync Helpers (non-blocking via asyncio.to_thread)
    # ------------------------------------------------------------------

    def _resolve_qdrant(self, client: Optional[QdrantClient]) -> Optional[QdrantClient]:
        if client is not None:
            return client
        try:
            from backend.db.qdrant import get_qdrant_client
            return get_qdrant_client()
        except Exception as exc:
            logger.warning("Qdrant unavailable for memory sync: %s", exc)
            return None

    async def _upsert_qdrant_point(
        self,
        client: Optional[QdrantClient],
        memory: UserMemory,
    ) -> bool:
        qclient = self._resolve_qdrant(client)
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
            point = models.PointStruct(
                id=str(memory.id),
                vector=embeddings[0],
                payload=payload,
            )
            await asyncio.to_thread(
                qclient.upsert,
                collection_name=self.collection_name,
                points=[point],
            )
            return True
        except Exception as exc:
            logger.warning("Qdrant upsert failed for memory %s (will retry in sweeper): %s", memory.id, exc)
            return False

    async def _delete_qdrant_points(
        self,
        client: Optional[QdrantClient],
        point_ids: Sequence[str],
    ) -> bool:
        if not point_ids:
            return True
        qclient = self._resolve_qdrant(client)
        if qclient is None:
            return False
        try:
            await asyncio.to_thread(
                qclient.delete,
                collection_name=self.collection_name,
                points_selector=models.PointIdsList(points=list(point_ids)),
            )
            return True
        except Exception as exc:
            logger.warning("Qdrant delete failed for %d memory points (will retry in sweeper): %s", len(point_ids), exc)
            return False

    # ------------------------------------------------------------------
    # CRUD & Lifecycle Operations (All strictly scoped to user_id)
    # ------------------------------------------------------------------

    async def create_memory(
        self,
        session: AsyncSession,
        client: Optional[QdrantClient],
        user_id: uuid.UUID,
        content: str,
        category: str = MemoryCategory.GENERAL.value,
        *,
        source_type: str = MemorySourceType.AUTO_EXTRACTED.value,
        confidence: float = 1.0,
        source_conversation_id: Optional[uuid.UUID] = None,
        source_message_id: Optional[uuid.UUID] = None,
        supersedes_memory_id: Optional[uuid.UUID] = None,
        expires_at: Optional[datetime] = None,
        memory_id: Optional[uuid.UUID] = None,
    ) -> UserMemory:
        now = datetime.now(timezone.utc)
        norm_cat = normalize_memory_category(category)
        mem = UserMemory(
            id=memory_id or uuid.uuid4(),
            user_id=user_id,
            content=content.strip(),
            category=norm_cat,
            status=MemoryStatus.ACTIVE.value,
            confidence=max(0.0, min(1.0, float(confidence))),
            source_type=source_type,
            source_conversation_id=source_conversation_id,
            source_message_id=source_message_id,
            supersedes_memory_id=supersedes_memory_id,
            version=1,
            sync_status=MemorySyncStatus.PENDING_UPSERT.value,
            expires_at=expires_at,
            created_at=now,
            updated_at=now,
        )
        session.add(mem)
        await session.commit()
        await session.refresh(mem)
        await self.invalidate_user_cache(user_id)

        if await self._upsert_qdrant_point(client, mem):
            mem.sync_status = MemorySyncStatus.SYNCED.value
            session.add(mem)
            await session.commit()
            await session.refresh(mem)

        return mem

    async def get_memory(
        self,
        session: AsyncSession,
        user_id: uuid.UUID,
        memory_id: uuid.UUID,
        include_deleted: bool = False,
    ) -> Optional[UserMemory]:
        stmt = select(UserMemory).where(
            UserMemory.id == memory_id,
            UserMemory.user_id == user_id,
        )
        if not include_deleted:
            stmt = stmt.where(UserMemory.status != MemoryStatus.DELETED.value)
        res = await session.execute(stmt)
        return res.scalar_one_or_none()

    async def update_memory(
        self,
        session: AsyncSession,
        client: Optional[QdrantClient],
        user_id: uuid.UUID,
        memory_id: uuid.UUID,
        new_content: str,
        category: Optional[str] = None,
        *,
        expected_version: Optional[int] = None,
    ) -> Optional[UserMemory]:
        mem = await self.get_memory(session, user_id, memory_id, include_deleted=False)
        if mem is None:
            return None
        if expected_version is not None and mem.version != expected_version:
            raise MemoryVersionConflictError(
                f"Memory {memory_id} was modified concurrently (expected v{expected_version}, found v{mem.version})"
            )

        clean_text = new_content.strip()
        if not clean_text:
            return None

        mem.content = clean_text
        if category is not None:
            mem.category = normalize_memory_category(category)
        mem.version += 1
        mem.updated_at = datetime.now(timezone.utc)
        mem.sync_status = MemorySyncStatus.PENDING_UPSERT.value
        session.add(mem)
        await session.commit()
        await session.refresh(mem)
        await self.invalidate_user_cache(user_id)

        if mem.status == MemoryStatus.ACTIVE.value:
            if await self._upsert_qdrant_point(client, mem):
                mem.sync_status = MemorySyncStatus.SYNCED.value
                session.add(mem)
                await session.commit()
                await session.refresh(mem)
        return mem

    async def supersede_memory(
        self,
        session: AsyncSession,
        client: Optional[QdrantClient],
        user_id: uuid.UUID,
        old_memory_id: uuid.UUID,
        new_content: str,
        category: Optional[str] = None,
        *,
        source_type: str = MemorySourceType.AUTO_EXTRACTED.value,
        confidence: float = 1.0,
        source_conversation_id: Optional[uuid.UUID] = None,
        source_message_id: Optional[uuid.UUID] = None,
        expected_version: Optional[int] = None,
    ) -> Optional[Tuple[UserMemory, UserMemory]]:
        """
        Marks the existing memory as SUPERSEDED (preserving historical lineage in PostgreSQL)
        and inserts a new ACTIVE memory pointing to `supersedes_memory_id = old.id`.
        Removes the superseded vector from Qdrant so active retrieval never returns stale facts.
        """
        old_mem = await self.get_memory(session, user_id, old_memory_id, include_deleted=False)
        if old_mem is None:
            return None
        if expected_version is not None and old_mem.version != expected_version:
            raise MemoryVersionConflictError(
                f"Memory {old_memory_id} was modified concurrently (expected v{expected_version}, found v{old_mem.version})"
            )

        now = datetime.now(timezone.utc)
        old_mem.status = MemoryStatus.SUPERSEDED.value
        old_mem.version += 1
        old_mem.updated_at = now
        old_mem.sync_status = MemorySyncStatus.PENDING_DELETE.value

        new_cat = normalize_memory_category(category or old_mem.category)
        new_mem = UserMemory(
            id=uuid.uuid4(),
            user_id=user_id,
            content=new_content.strip(),
            category=new_cat,
            status=MemoryStatus.ACTIVE.value,
            confidence=max(0.0, min(1.0, float(confidence))),
            source_type=source_type,
            source_conversation_id=source_conversation_id,
            source_message_id=source_message_id,
            supersedes_memory_id=old_mem.id,
            version=1,
            sync_status=MemorySyncStatus.PENDING_UPSERT.value,
            created_at=now,
            updated_at=now,
        )
        session.add(old_mem)
        session.add(new_mem)
        await session.commit()
        await session.refresh(old_mem)
        await session.refresh(new_mem)
        await self.invalidate_user_cache(user_id)

        old_deleted = await self._delete_qdrant_points(client, [str(old_mem.id)])
        new_upserted = await self._upsert_qdrant_point(client, new_mem)
        dirty = False
        if old_deleted:
            old_mem.sync_status = MemorySyncStatus.SYNCED.value
            session.add(old_mem)
            dirty = True
        if new_upserted:
            new_mem.sync_status = MemorySyncStatus.SYNCED.value
            session.add(new_mem)
            dirty = True
        if dirty:
            await session.commit()
            await session.refresh(old_mem)
            await session.refresh(new_mem)

        return old_mem, new_mem

    async def soft_delete_memory(
        self,
        session: AsyncSession,
        client: Optional[QdrantClient],
        user_id: uuid.UUID,
        memory_id: uuid.UUID,
    ) -> bool:
        mem = await self.get_memory(session, user_id, memory_id, include_deleted=False)
        if mem is None:
            return False

        now = datetime.now(timezone.utc)
        mem.status = MemoryStatus.DELETED.value
        mem.deleted_at = now
        mem.updated_at = now
        mem.version += 1
        mem.sync_status = MemorySyncStatus.PENDING_DELETE.value
        session.add(mem)
        await session.commit()
        await self.invalidate_user_cache(user_id)

        if await self._delete_qdrant_points(client, [str(mem.id)]):
            mem.sync_status = MemorySyncStatus.SYNCED.value
            session.add(mem)
            await session.commit()

        return True

    async def bulk_delete_memories(
        self,
        session: AsyncSession,
        client: Optional[QdrantClient],
        user_id: uuid.UUID,
        *,
        category: Optional[str] = None,
        memory_ids: Optional[Sequence[uuid.UUID]] = None,
        delete_all: bool = False,
    ) -> List[UserMemory]:
        """
        Soft-deletes matching memories owned by `user_id`. Idempotent: returns empty list if none match.
        Requires at least one explicit selector (`category`, `memory_ids`, or `delete_all=True`).
        """
        if not delete_all and not category and not memory_ids:
            return []

        conditions = [
            UserMemory.user_id == user_id,
            UserMemory.status != MemoryStatus.DELETED.value,
        ]
        if category:
            conditions.append(UserMemory.category == normalize_memory_category(category))
        if memory_ids:
            conditions.append(col(UserMemory.id).in_(list(memory_ids)))

        stmt = select(UserMemory).where(*conditions)
        targets = list((await session.execute(stmt)).scalars().all())
        if not targets:
            return []

        now = datetime.now(timezone.utc)
        for mem in targets:
            mem.status = MemoryStatus.DELETED.value
            mem.deleted_at = now
            mem.updated_at = now
            mem.version += 1
            mem.sync_status = MemorySyncStatus.PENDING_DELETE.value
            session.add(mem)
        await session.commit()
        await self.invalidate_user_cache(user_id)

        point_ids = [str(m.id) for m in targets]
        if await self._delete_qdrant_points(client, point_ids):
            for mem in targets:
                mem.sync_status = MemorySyncStatus.SYNCED.value
                session.add(mem)
            await session.commit()

        return targets

    # ------------------------------------------------------------------
    # Querying & Semantic Retrieval (Always PG-verified against active rows)
    # ------------------------------------------------------------------

    async def list_memories(
        self,
        session: AsyncSession,
        user_id: uuid.UUID,
        *,
        category: Optional[str] = None,
        search: Optional[str] = None,
        status: str = MemoryStatus.ACTIVE.value,
        limit: int = 25,
        offset: int = 0,
    ) -> Tuple[List[UserMemory], int]:
        """Authoritative PostgreSQL listing with filtering, keyword search, and pagination."""
        now = datetime.now(timezone.utc)
        conditions: List[Any] = [UserMemory.user_id == user_id]

        if status == "all":
            conditions.append(UserMemory.status != MemoryStatus.DELETED.value)
        elif status == MemoryStatus.SUPERSEDED.value:
            conditions.append(UserMemory.status == MemoryStatus.SUPERSEDED.value)
        else:
            conditions.append(UserMemory.status == MemoryStatus.ACTIVE.value)
            conditions.append(or_(UserMemory.expires_at == None, UserMemory.expires_at > now))  # noqa: E711

        if category:
            conditions.append(UserMemory.category == normalize_memory_category(category))

        if search and search.strip():
            term = f"%{search.strip()}%"
            conditions.append(
                or_(
                    col(UserMemory.content).ilike(term),
                    col(UserMemory.category).ilike(term),
                )
            )

        count_stmt = select(func.count(UserMemory.id)).where(*conditions)
        total = int((await session.execute(count_stmt)).scalar_one() or 0)

        data_stmt = (
            select(UserMemory)
            .where(*conditions)
            .order_by(col(UserMemory.updated_at).desc(), col(UserMemory.created_at).desc())
            .offset(max(0, offset))
            .limit(max(1, limit))
        )
        items = list((await session.execute(data_stmt)).scalars().all())
        return items, total

    async def get_recent_active_by_categories(
        self,
        session: AsyncSession,
        user_id: uuid.UUID,
        categories: Sequence[str],
        limit: int = 5,
    ) -> List[UserMemory]:
        now = datetime.now(timezone.utc)
        norm_cats = [normalize_memory_category(c) for c in categories]
        stmt = (
            select(UserMemory)
            .where(
                UserMemory.user_id == user_id,
                UserMemory.status == MemoryStatus.ACTIVE.value,
                col(UserMemory.category).in_(norm_cats),
                or_(UserMemory.expires_at == None, UserMemory.expires_at > now),  # noqa: E711
            )
            .order_by(col(UserMemory.updated_at).desc())
            .limit(limit)
        )
        return list((await session.execute(stmt)).scalars().all())

    async def semantic_search(
        self,
        session: AsyncSession,
        client: Optional[QdrantClient],
        user_id: uuid.UUID,
        query: str,
        *,
        limit: int = 5,
        threshold: float = 0.52,
        categories: Optional[Sequence[str]] = None,
    ) -> List[Tuple[UserMemory, float]]:
        """
        Semantic search via Qdrant filtered by `user_id` (+ optional categories),
        then cross-verified against active PostgreSQL rows so deleted or superseded
        memories can NEVER be returned even if Qdrant deletion failed transiently.
        """
        if not query or not query.strip():
            return []

        qclient = self._resolve_qdrant(client)
        if qclient is None:
            return []

        try:
            embeddings = await asyncio.to_thread(generate_dense_embeddings, [query.strip()])
            if not embeddings:
                return []
            query_vector = embeddings[0]

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
                collection_name=self.collection_name,
                query=query_vector,
                query_filter=models.Filter(must=must_conditions),
                limit=max(limit * 2, 10),
                score_threshold=threshold,
            )
            points = results.points if hasattr(results, "points") else results
        except Exception as exc:
            logger.warning("Qdrant semantic memory search failed (%s); falling back safely", exc)
            return []

        if not points:
            return []

        score_by_id: Dict[uuid.UUID, float] = {}
        ordered_ids: List[uuid.UUID] = []
        for hit in points:
            try:
                pid = uuid.UUID(str(hit.id))
            except ValueError:
                continue
            if pid not in score_by_id:
                score_by_id[pid] = float(getattr(hit, "score", 0.0) or 0.0)
                ordered_ids.append(pid)

        if not ordered_ids:
            return []

        now = datetime.now(timezone.utc)
        stmt = select(UserMemory).where(
            UserMemory.user_id == user_id,
            UserMemory.status == MemoryStatus.ACTIVE.value,
            col(UserMemory.id).in_(ordered_ids),
            or_(UserMemory.expires_at == None, UserMemory.expires_at > now),  # noqa: E711
        )
        active_rows = {m.id: m for m in (await session.execute(stmt)).scalars().all()}

        verified: List[Tuple[UserMemory, float]] = []
        for pid in ordered_ids:
            mem = active_rows.get(pid)
            if mem is not None:
                verified.append((mem, score_by_id[pid]))
                if len(verified) >= limit:
                    break
        return verified

    async def backfill_qdrant_to_pg(
        self,
        session: AsyncSession,
        client: Optional[QdrantClient],
        user_id: uuid.UUID,
    ) -> int:
        """
        Idempotent helper to import legacy Qdrant-only memories for a user into PostgreSQL.
        Returns number of memories imported.
        """
        qclient = self._resolve_qdrant(client)
        if qclient is None:
            return 0
        try:
            scroll_res = await asyncio.to_thread(
                qclient.scroll,
                collection_name=self.collection_name,
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
            mem = UserMemory(
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
            session.add(mem)
            imported += 1

        if imported:
            await session.commit()
            await self.invalidate_user_cache(user_id)
            logger.info("Backfilled %d legacy Qdrant memories into PostgreSQL for user %s", imported, user_id)
        return imported

    def serialize_memory(self, mem: UserMemory, score: Optional[float] = None) -> Dict[str, Any]:
        return {
            "memory_id": str(mem.id),
            "text": mem.content,
            "category": mem.category,
            "status": mem.status,
            "confidence": mem.confidence,
            "source_type": mem.source_type,
            "supersedes_memory_id": str(mem.supersedes_memory_id) if mem.supersedes_memory_id else None,
            "version": mem.version,
            "created_at": mem.created_at.isoformat() if mem.created_at else None,
            "updated_at": mem.updated_at.isoformat() if mem.updated_at else None,
            "score": score,
        }


memory_store = MemoryStore()
