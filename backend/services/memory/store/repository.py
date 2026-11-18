from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple
import uuid

from qdrant_client import QdrantClient
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, or_, select

from backend.core.config import settings
from backend.models.memory import (
    MemoryCategory,
    MemorySourceType,
    MemoryStatus,
    MemorySyncStatus,
    UserMemory,
    normalize_memory_category,
)
from backend.services.memory.store.cache import (
    get_cached_active_non_pref_count,
    get_cached_active_preferences,
    invalidate_user_memory_cache,
)
from backend.services.memory.store.qdrant_sync import (
    backfill_qdrant_memories_to_pg,
    delete_qdrant_memory_points,
    query_qdrant_memory_points,
    upsert_qdrant_memory_point,
)


class MemoryVersionConflictError(Exception):
    """Raised when a concurrent mutation detects a stale memory version."""


class MemoryStore:
    """
    Canonical Memory Persistence Layer.
    - PostgreSQL (`user_memories`) is the authoritative source of truth.
    - Qdrant (`cortex_user_memories`) is the semantic vector index (same UUID per memory).
    - Redis caches per-user active preferences and active non-preference counts.
    """

    def __init__(self, collection_name: Optional[str] = None) -> None:
        self.collection_name = collection_name or settings.QDRANT_USER_MEMORIES_COLLECTION

    async def invalidate_user_cache(self, user_id: uuid.UUID) -> None:
        await invalidate_user_memory_cache(user_id)

    async def get_active_preferences(self, session: AsyncSession, user_id: uuid.UUID, limit: int = 8) -> List[str]:
        return await get_cached_active_preferences(session, user_id, limit=limit)

    async def get_active_non_pref_count(self, session: AsyncSession, user_id: uuid.UUID) -> int:
        return await get_cached_active_non_pref_count(session, user_id)

    async def _upsert_qdrant_point(self, client: Optional[QdrantClient], memory: UserMemory) -> bool:
        return await upsert_qdrant_memory_point(client, self.collection_name, memory)

    async def _delete_qdrant_points(self, client: Optional[QdrantClient], point_ids: Sequence[str]) -> bool:
        return await delete_qdrant_memory_points(client, self.collection_name, point_ids)

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
        mem = UserMemory(
            id=memory_id or uuid.uuid4(),
            user_id=user_id,
            content=content.strip(),
            category=normalize_memory_category(category),
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
        stmt = select(UserMemory).where(UserMemory.id == memory_id, UserMemory.user_id == user_id)
        if not include_deleted:
            stmt = stmt.where(UserMemory.status != MemoryStatus.DELETED.value)
        return (await session.execute(stmt)).scalar_one_or_none()

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

        if mem.status == MemoryStatus.ACTIVE.value and await self._upsert_qdrant_point(client, mem):
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

        new_mem = UserMemory(
            id=uuid.uuid4(),
            user_id=user_id,
            content=new_content.strip(),
            category=normalize_memory_category(category or old_mem.category),
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

        dirty = False
        if await self._delete_qdrant_points(client, [str(old_mem.id)]):
            old_mem.sync_status = MemorySyncStatus.SYNCED.value
            session.add(old_mem)
            dirty = True
        if await self._upsert_qdrant_point(client, new_mem):
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
        if not delete_all and not category and not memory_ids:
            return []

        conditions: List[Any] = [
            UserMemory.user_id == user_id,
            UserMemory.status != MemoryStatus.DELETED.value,
        ]
        if category:
            conditions.append(UserMemory.category == normalize_memory_category(category))
        if memory_ids:
            conditions.append(col(UserMemory.id).in_(list(memory_ids)))

        targets = list((await session.execute(select(UserMemory).where(*conditions))).scalars().all())
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

        if await self._delete_qdrant_points(client, [str(m.id) for m in targets]):
            for mem in targets:
                mem.sync_status = MemorySyncStatus.SYNCED.value
                session.add(mem)
            await session.commit()
        return targets

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
            conditions.append(or_(col(UserMemory.content).ilike(term), col(UserMemory.category).ilike(term)))

        total = int((await session.execute(select(func.count(UserMemory.id)).where(*conditions))).scalar_one() or 0)
        stmt = (
            select(UserMemory)
            .where(*conditions)
            .order_by(col(UserMemory.updated_at).desc(), col(UserMemory.created_at).desc())
            .offset(max(0, offset))
            .limit(max(1, limit))
        )
        items = list((await session.execute(stmt)).scalars().all())
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
        points = await query_qdrant_memory_points(
            client,
            self.collection_name,
            user_id,
            query,
            limit=limit,
            threshold=threshold,
            categories=categories,
        )
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
        return await backfill_qdrant_memories_to_pg(session, client, self.collection_name, user_id)

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
