import logging
from typing import List, Optional

from qdrant_client import QdrantClient
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.memory import MemoryCategory
from backend.models.user import User
from backend.services.memory.command_detector import has_broad_personal_cue, is_trivial_message
from backend.services.memory.manager.types import MEMORY_RETRIEVAL_THRESHOLD
from backend.services.memory.store.repository import MemoryStore

logger = logging.getLogger(__name__)


async def retrieve_relevant_user_memories(
    store: MemoryStore,
    session: AsyncSession,
    client: Optional[QdrantClient],
    user: User,
    query: str,
    *,
    max_preferences: int = 8,
    max_semantic: int = 5,
) -> List[str]:
    """
    Zero-LLM-cost hybrid retrieval:
      1. Always load active `preference` memories (Redis-cached, <3ms).
      2. Fast-exit (<1ms) if the user has 0 active non-preference memories.
      3. Skip vector search on tiny trivial greetings/acknowledgments.
      4. Run local ONNX FastEmbed + Qdrant search (~25ms, PG-verified active rows).
      5. Broad-personal PG fallback (0 LLM calls) when indirect phrasing has low semantic overlap.
    """
    formatted: List[str] = []
    seen_texts: set[str] = set()

    try:
        prefs = await store.get_active_preferences(session=session, user_id=user.id, limit=max_preferences)
        for p in prefs:
            if p not in seen_texts:
                seen_texts.add(p)
                formatted.append(f"[preference] {p}")
    except Exception as exc:
        logger.warning("Failed to load user preferences (%s); continuing", exc)

    if not query or is_trivial_message(query):
        return formatted

    try:
        non_pref_count = await store.get_active_non_pref_count(session=session, user_id=user.id)
    except Exception as exc:
        logger.warning("Failed to count active user memories (%s); continuing", exc)
        return formatted

    if non_pref_count == 0:
        return formatted

    semantic_hits = await store.semantic_search(
        session=session,
        client=client,
        user_id=user.id,
        query=query,
        limit=max_semantic,
        threshold=MEMORY_RETRIEVAL_THRESHOLD,
    )
    for mem, _score in semantic_hits:
        if mem.content not in seen_texts:
            seen_texts.add(mem.content)
            formatted.append(f"[{mem.category}] {mem.content}")

    if len(semantic_hits) < 2 and has_broad_personal_cue(query):
        recent_personal = await store.get_recent_active_by_categories(
            session=session,
            user_id=user.id,
            categories=[
                MemoryCategory.PROJECT_CONTEXT.value,
                MemoryCategory.TECH_STACK.value,
                MemoryCategory.GOAL.value,
                MemoryCategory.WORKFLOW.value,
            ],
            limit=max_semantic,
        )
        for mem in recent_personal:
            if mem.content not in seen_texts:
                seen_texts.add(mem.content)
                formatted.append(f"[{mem.category}] {mem.content}")

    return formatted[:10]
