import logging
from typing import Any, List, Optional
import uuid

from qdrant_client import QdrantClient
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.config import settings
from backend.db.database import async_session
from backend.models.memory import MemoryCategory, MemorySourceType, UserMemory
from backend.services.memory.command_detector import should_attempt_background_extraction
from backend.services.memory.manager.types import EXACT_DUPLICATE_THRESHOLD, format_existing_memories
from backend.services.memory.privacy_guard import validate_memory_content
from backend.services.memory.prompts import (
    AUTO_RECONCILE_SYSTEM_PROMPT_V1,
    MemoryReconciliationPlan,
    ReconcileActionType,
)
from backend.services.memory.store.repository import MemoryStore

logger = logging.getLogger(__name__)


async def run_extract_and_reconcile(
    store: MemoryStore,
    llm_gateway: Any,
    session: AsyncSession,
    client: Optional[QdrantClient],
    user_id: uuid.UUID,
    user_message: str,
    *,
    recent_user_message: Optional[str] = None,
    conversation_id: Optional[uuid.UUID] = None,
    message_id: Optional[uuid.UUID] = None,
) -> List[UserMemory]:
    """
    Extracts durable facts and reconciles them against related existing memories
    in ONE structured LLM call (ADD / UPDATE / SUPERSEDE / NOOP).
    """
    if not should_attempt_background_extraction(user_message) or not llm_gateway.is_configured():
        return []

    related_hits = await store.semantic_search(
        session=session,
        client=client,
        user_id=user_id,
        query=user_message,
        limit=5,
        threshold=0.38,
    )
    related_memories = [m for m, _ in related_hits]
    owned_by_id = {str(m.id): m for m in related_memories}

    prompt = (
        f"Previous User Message: {recent_user_message or '(none)'}\n\n"
        f"Existing Related Memories:\n{format_existing_memories(related_memories)}\n\n"
        f"Current User Message: {user_message}"
    )

    try:
        plan = await llm_gateway.generate_structured(
            prompt=prompt,
            response_model=MemoryReconciliationPlan,
            system_prompt=AUTO_RECONCILE_SYSTEM_PROMPT_V1,
            model=settings.FAST_ROUTING_MODEL,
            temperature=0.0,
            user_id=user_id,
            session=session,
        )
    except Exception as exc:
        logger.warning("Background memory extraction LLM failed (%s); skipping safely", exc)
        return []

    if not plan or not plan.actions:
        return []

    mutated: List[UserMemory] = []
    for action in plan.actions:
        if action.action == ReconcileActionType.NOOP:
            continue

        validation = validate_memory_content((action.content or "").strip(), is_explicit=False)
        if not validation.allowed:
            logger.info("Background memory skipped by privacy guard for user %s: %s", user_id, validation.rejection_reason)
            continue

        try:
            if action.action == ReconcileActionType.SUPERSEDE and action.target_memory_id in owned_by_id:
                target = owned_by_id[action.target_memory_id]
                pair = await store.supersede_memory(
                    session=session,
                    client=client,
                    user_id=user_id,
                    old_memory_id=target.id,
                    new_content=validation.sanitized_content,
                    category=action.category or target.category,
                    source_type=MemorySourceType.AUTO_EXTRACTED.value,
                    confidence=action.confidence,
                    source_conversation_id=conversation_id,
                    source_message_id=message_id,
                )
                if pair:
                    mutated.append(pair[1])
            elif action.action == ReconcileActionType.UPDATE and action.target_memory_id in owned_by_id:
                target = owned_by_id[action.target_memory_id]
                updated = await store.update_memory(
                    session=session,
                    client=client,
                    user_id=user_id,
                    memory_id=target.id,
                    new_content=validation.sanitized_content,
                    category=action.category or target.category,
                )
                if updated:
                    mutated.append(updated)
            else:
                dup_hits = await store.semantic_search(
                    session=session,
                    client=client,
                    user_id=user_id,
                    query=validation.sanitized_content,
                    limit=1,
                    threshold=EXACT_DUPLICATE_THRESHOLD,
                )
                if dup_hits:
                    continue
                created = await store.create_memory(
                    session=session,
                    client=client,
                    user_id=user_id,
                    content=validation.sanitized_content,
                    category=action.category or MemoryCategory.GENERAL.value,
                    source_type=MemorySourceType.AUTO_EXTRACTED.value,
                    confidence=action.confidence,
                    source_conversation_id=conversation_id,
                    source_message_id=message_id,
                )
                mutated.append(created)
        except Exception as exc:
            logger.warning("Failed to persist background memory action %s: %s", action.action.value, exc)

    return mutated


async def run_extract_and_reconcile_background(
    store: MemoryStore,
    llm_gateway: Any,
    client: Optional[QdrantClient],
    user_id: uuid.UUID,
    user_message: str,
    *,
    recent_user_message: Optional[str] = None,
    conversation_id: Optional[uuid.UUID] = None,
    message_id: Optional[uuid.UUID] = None,
) -> None:
    """Isolated fire-and-forget post-turn extraction using its own AsyncSession."""
    try:
        async with async_session() as bg_session:
            await run_extract_and_reconcile(
                store=store,
                llm_gateway=llm_gateway,
                session=bg_session,
                client=client,
                user_id=user_id,
                user_message=user_message,
                recent_user_message=recent_user_message,
                conversation_id=conversation_id,
                message_id=message_id,
            )
    except Exception as exc:
        logger.warning("Background memory extraction task failed safely: %s", exc)
