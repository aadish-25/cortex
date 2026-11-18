import logging
from typing import Any, Dict, List, Optional
import uuid

from qdrant_client import QdrantClient

from backend.core.config import settings
from backend.db.database import async_session
from backend.models.memory import MemoryCategory, MemorySourceType, MemoryStatus
from backend.services.memory.command_detector import detect_explicit_memory_command
from backend.services.memory.memory_manager import memory_manager
from backend.services.memory.memory_store import memory_store
from backend.services.memory.privacy_guard import validate_memory_content

logger = logging.getLogger(__name__)


class EpisodicMemoryService:
    """
    Backward-compatible facade delegating all Tier 2 Long-Term Memory operations
    to the canonical PostgreSQL + Qdrant `memory_store` and independent `memory_manager`.
    Preserves existing service signatures without introducing a competing memory store.
    """

    def __init__(self, collection_name: Optional[str] = None) -> None:
        self.collection_name = collection_name or settings.QDRANT_USER_MEMORIES_COLLECTION

    async def get_user_preferences(
        self,
        client: Optional[QdrantClient],
        user_id: uuid.UUID,
        limit: int = 8,
    ) -> List[str]:
        async with async_session() as session:
            return await memory_store.get_active_preferences(session=session, user_id=user_id, limit=limit)

    async def search_memories(
        self,
        client: Optional[QdrantClient],
        user_id: uuid.UUID,
        query: str,
        limit: int = 5,
        threshold: float = 0.52,
    ) -> List[str]:
        async with async_session() as session:
            hits = await memory_store.semantic_search(
                session=session,
                client=client,
                user_id=user_id,
                query=query,
                limit=limit,
                threshold=threshold,
            )
            return [m.content for m, _score in hits]

    async def _store_single_fact(
        self,
        client: Optional[QdrantClient],
        user_id: uuid.UUID,
        fact_text: str,
        category: str = "preference",
    ) -> Optional[str]:
        validation = validate_memory_content(fact_text, is_explicit=True)
        if not validation.allowed:
            return None
        async with async_session() as session:
            dup = await memory_store.semantic_search(
                session=session,
                client=client,
                user_id=user_id,
                query=validation.sanitized_content,
                limit=1,
                threshold=0.88,
            )
            if dup:
                return None
            created = await memory_store.create_memory(
                session=session,
                client=client,
                user_id=user_id,
                content=validation.sanitized_content,
                category=category or MemoryCategory.GENERAL.value,
                source_type=MemorySourceType.AUTO_EXTRACTED.value,
            )
            return created.content

    async def extract_and_store_memories(
        self,
        client: Optional[QdrantClient],
        user_id: uuid.UUID,
        user_message: str,
        recent_user_message: Optional[str] = None,
    ) -> List[str]:
        if not user_message or not user_message.strip():
            return []
        async with async_session() as session:
            # Delegate to memory_manager's combined extraction + conflict resolution
            mutated = await memory_manager.extract_and_reconcile(
                session=session,
                client=client,
                user_id=user_id,
                user_message=user_message,
                recent_user_message=recent_user_message,
            )
            if mutated:
                return [m.content for m in mutated]

            # Deterministic safety net when offline and user explicitly said remember/add
            signal = detect_explicit_memory_command(user_message)
            if signal.is_explicit and "ADD" in signal.hinted_ops:
                validation = validate_memory_content(user_message, is_explicit=True)
                if validation.allowed:
                    stored = await self._store_single_fact(client, user_id, validation.sanitized_content, "preference")
                    if stored:
                        return [stored]
            return []

    async def list_memories_page(
        self,
        client: Optional[QdrantClient],
        user_id: uuid.UUID,
        limit: int = 50,
        offset: Optional[str] = None,
    ) -> Dict[str, Any]:
        numeric_offset = 0
        if offset:
            try:
                numeric_offset = max(0, int(offset))
            except ValueError:
                numeric_offset = 0

        async with async_session() as session:
            items, total = await memory_store.list_memories(
                session=session,
                user_id=user_id,
                status=MemoryStatus.ACTIVE.value,
                limit=limit,
                offset=numeric_offset,
            )
            next_offset = numeric_offset + len(items)
            return {
                "items": [
                    {
                        "id": str(m.id),
                        "fact": m.content,
                        "category": m.category,
                        "status": m.status,
                        "created_at": m.created_at.isoformat() if m.created_at else None,
                        "updated_at": m.updated_at.isoformat() if m.updated_at else None,
                    }
                    for m in items
                ],
                "total": total,
                "next_cursor": str(next_offset) if next_offset < total else None,
            }

    async def list_memories(
        self,
        client: Optional[QdrantClient],
        user_id: uuid.UUID,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        result = await self.list_memories_page(client=client, user_id=user_id, limit=limit)
        return result["items"]

    async def update_memory(
        self,
        client: Optional[QdrantClient],
        user_id: uuid.UUID,
        memory_id: str,
        new_text: str,
        category: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        try:
            mem_uuid = uuid.UUID(str(memory_id))
        except ValueError:
            return None
        validation = validate_memory_content(new_text, is_explicit=True)
        if not validation.allowed:
            return None
        async with async_session() as session:
            updated = await memory_store.update_memory(
                session=session,
                client=client,
                user_id=user_id,
                memory_id=mem_uuid,
                new_content=validation.sanitized_content,
                category=category,
            )
            if not updated:
                return None
            return memory_store.serialize_memory(updated)

    async def delete_memory(
        self,
        client: Optional[QdrantClient],
        user_id: uuid.UUID,
        memory_id: str,
    ) -> bool:
        try:
            mem_uuid = uuid.UUID(str(memory_id))
        except ValueError:
            return False
        async with async_session() as session:
            return await memory_store.soft_delete_memory(
                session=session,
                client=client,
                user_id=user_id,
                memory_id=mem_uuid,
            )


episodic_memory_service = EpisodicMemoryService()
