from typing import List, Optional
import uuid

from qdrant_client import QdrantClient
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.memory import UserMemory
from backend.models.user import User
from backend.services.memory.manager.command_executor import execute_explicit_command
from backend.services.memory.manager.reconciler import (
    run_extract_and_reconcile,
    run_extract_and_reconcile_background,
)
from backend.services.memory.manager.retriever import retrieve_relevant_user_memories
from backend.services.memory.manager.types import (
    EXACT_DUPLICATE_THRESHOLD,
    MEMORY_RETRIEVAL_THRESHOLD,
    MemoryCommandResult,
)
from backend.services.memory.memory_store import MemoryStore, memory_store
from backend.services.orchestration.llm_service import llm_service


class MemoryManagerService:
    """
    Independent Intelligent Memory Management System facade for Cortex.
    Delegates cleanly to modular `manager/*` components:
      - `command_executor.py`: explicit ADD/UPDATE/SUPERSEDE/DELETE/RETRIEVE + compound turns
      - `retriever.py`: zero-LLM-cost hybrid retrieval (Redis prefs + fast-exit + local Qdrant + PG fallback)
      - `reconciler.py`: async post-turn extraction & conflict resolution
    """

    def __init__(self, store: Optional[MemoryStore] = None) -> None:
        self.store = store or memory_store

    async def handle_explicit_command(
        self,
        session: AsyncSession,
        client: Optional[QdrantClient],
        user: User,
        user_message: str,
        *,
        recent_user_message: Optional[str] = None,
        conversation_id: Optional[uuid.UUID] = None,
        message_id: Optional[uuid.UUID] = None,
    ) -> MemoryCommandResult:
        return await execute_explicit_command(
            store=self.store,
            llm_gateway=llm_service,
            session=session,
            client=client,
            user=user,
            user_message=user_message,
            recent_user_message=recent_user_message,
            conversation_id=conversation_id,
            message_id=message_id,
        )

    async def retrieve_relevant_memories(
        self,
        session: AsyncSession,
        client: Optional[QdrantClient],
        user: User,
        query: str,
        *,
        max_preferences: int = 8,
        max_semantic: int = 5,
    ) -> List[str]:
        return await retrieve_relevant_user_memories(
            store=self.store,
            session=session,
            client=client,
            user=user,
            query=query,
            max_preferences=max_preferences,
            max_semantic=max_semantic,
        )

    async def extract_and_reconcile(
        self,
        session: AsyncSession,
        client: Optional[QdrantClient],
        user_id: uuid.UUID,
        user_message: str,
        *,
        recent_user_message: Optional[str] = None,
        conversation_id: Optional[uuid.UUID] = None,
        message_id: Optional[uuid.UUID] = None,
    ) -> List[UserMemory]:
        return await run_extract_and_reconcile(
            store=self.store,
            llm_gateway=llm_service,
            session=session,
            client=client,
            user_id=user_id,
            user_message=user_message,
            recent_user_message=recent_user_message,
            conversation_id=conversation_id,
            message_id=message_id,
        )

    async def extract_and_reconcile_background(
        self,
        client: Optional[QdrantClient],
        user_id: uuid.UUID,
        user_message: str,
        *,
        recent_user_message: Optional[str] = None,
        conversation_id: Optional[uuid.UUID] = None,
        message_id: Optional[uuid.UUID] = None,
    ) -> None:
        await run_extract_and_reconcile_background(
            store=self.store,
            llm_gateway=llm_service,
            client=client,
            user_id=user_id,
            user_message=user_message,
            recent_user_message=recent_user_message,
            conversation_id=conversation_id,
            message_id=message_id,
        )


memory_manager = MemoryManagerService()

__all__ = [
    "EXACT_DUPLICATE_THRESHOLD",
    "MEMORY_RETRIEVAL_THRESHOLD",
    "MemoryCommandResult",
    "MemoryManagerService",
    "memory_manager",
]
