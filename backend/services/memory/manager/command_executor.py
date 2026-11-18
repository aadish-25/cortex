import logging
from typing import Any, Dict, List, Optional
import uuid

from qdrant_client import QdrantClient
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.config import settings
from backend.models.memory import (
    MemoryCategory,
    MemorySourceType,
    MemoryStatus,
    UserMemory,
    normalize_memory_category,
)
from backend.models.user import User
from backend.services.memory.command_detector import detect_explicit_memory_command
from backend.services.memory.manager.fallback_parser import build_deterministic_command_plan
from backend.services.memory.manager.types import (
    EXACT_DUPLICATE_THRESHOLD,
    MemoryCommandResult,
    build_factual_summary,
    format_existing_memories,
)
from backend.services.memory.privacy_guard import validate_memory_content
from backend.services.memory.prompts import (
    EXPLICIT_COMMAND_SYSTEM_PROMPT_V1,
    ExplicitMemoryCommandPlan,
    MemoryOpType,
    PlannedMemoryOperation,
)
from backend.services.memory.store.repository import MemoryStore

logger = logging.getLogger(__name__)


async def execute_explicit_command(
    store: MemoryStore,
    llm_gateway: Any,
    session: AsyncSession,
    client: Optional[QdrantClient],
    user: User,
    user_message: str,
    *,
    recent_user_message: Optional[str] = None,
    conversation_id: Optional[uuid.UUID] = None,
    message_id: Optional[uuid.UUID] = None,
) -> MemoryCommandResult:
    """Detects and executes explicit user-directed memory commands strictly scoped to `user.id`."""
    signal = detect_explicit_memory_command(user_message)
    if not signal.is_explicit:
        return MemoryCommandResult(handled=False)

    active_memories, _ = await store.list_memories(
        session=session,
        user_id=user.id,
        status=MemoryStatus.ACTIVE.value,
        limit=50,
        offset=0,
    )
    owned_by_id: Dict[str, UserMemory] = {str(m.id): m for m in active_memories}

    plan: Optional[ExplicitMemoryCommandPlan] = None
    if llm_gateway.is_configured():
        prompt = (
            f"Detected Explicit Command Hints: {', '.join(signal.hinted_ops)}\n"
            f"Previous User Message: {recent_user_message or '(none)'}\n\n"
            f"Existing User Memories:\n{format_existing_memories(active_memories)}\n\n"
            f"Current User Message: {user_message}"
        )
        try:
            plan = await llm_gateway.generate_structured(
                prompt=prompt,
                response_model=ExplicitMemoryCommandPlan,
                system_prompt=EXPLICIT_COMMAND_SYSTEM_PROMPT_V1,
                model=settings.FAST_ROUTING_MODEL,
                temperature=0.0,
                user_id=user.id,
                session=session,
            )
        except Exception as exc:
            logger.warning("Explicit memory command LLM parser failed (%s); using deterministic fallback", exc)

    if plan is None or not plan.operations:
        plan = await build_deterministic_command_plan(
            store=store,
            session=session,
            client=client,
            user=user,
            user_message=user_message,
            recent_user_message=recent_user_message,
            hinted_ops=signal.hinted_ops,
            active_memories=active_memories,
        )

    actionable_ops = [op for op in plan.operations if op.op != MemoryOpType.NONE]
    if not actionable_ops:
        return MemoryCommandResult(handled=False)

    result = MemoryCommandResult(
        handled=True,
        is_pure_memory_command=bool(plan.is_pure_memory_command and not (plan.remaining_user_query or "").strip()),
        remaining_user_query=plan.remaining_user_query.strip() if plan.remaining_user_query else None,
    )

    for op in actionable_ops:
        try:
            await _execute_single_op(
                store=store,
                session=session,
                client=client,
                user=user,
                op=op,
                owned_by_id=owned_by_id,
                conversation_id=conversation_id,
                message_id=message_id,
                result=result,
            )
        except Exception as exc:
            logger.error("Memory operation %s failed for user %s: %s", op.op.value, user.id, exc, exc_info=True)
            result.failed.append(f"Could not complete memory {op.op.value.lower()} due to a storage error.")

    result.factual_summary = build_factual_summary(result)
    return result


async def _execute_single_op(
    store: MemoryStore,
    session: AsyncSession,
    client: Optional[QdrantClient],
    user: User,
    op: PlannedMemoryOperation,
    owned_by_id: Dict[str, UserMemory],
    conversation_id: Optional[uuid.UUID],
    message_id: Optional[uuid.UUID],
    result: MemoryCommandResult,
) -> None:
    if op.op == MemoryOpType.CLARIFY:
        result.actions_performed.append("CLARIFY")
        result.clarification_question = (
            op.clarification_question
            or "Could you clarify which specific memory you would like me to update or delete?"
        )
        return

    if op.op == MemoryOpType.RETRIEVE:
        result.actions_performed.append("RETRIEVE")
        norm_cat = normalize_memory_category(op.category) if op.category else None
        rows, _ = await store.list_memories(
            session=session,
            user_id=user.id,
            category=norm_cat,
            search=op.search_query,
            status=MemoryStatus.ACTIVE.value,
            limit=50,
            offset=0,
        )
        if not rows and op.search_query:
            sem_hits = await store.semantic_search(
                session=session,
                client=client,
                user_id=user.id,
                query=op.search_query,
                limit=10,
                threshold=0.42,
                categories=[norm_cat] if norm_cat else None,
            )
            rows = [m for m, _ in sem_hits]
        result.retrieved.extend(store.serialize_memory(m) for m in rows)
        return

    if op.op == MemoryOpType.ADD:
        candidate = (op.content or "").strip()
        validation = validate_memory_content(candidate, is_explicit=True)
        if not validation.allowed:
            result.actions_performed.append("REJECTED")
            result.rejected.append({
                "content": candidate[:120],
                "reason": validation.rejection_reason or "Rejected by memory privacy policy.",
            })
            return

        existing_hits = await store.semantic_search(
            session=session,
            client=client,
            user_id=user.id,
            query=validation.sanitized_content,
            limit=1,
            threshold=EXACT_DUPLICATE_THRESHOLD,
        )
        if existing_hits:
            result.actions_performed.append("ADD")
            result.already_existed.append(existing_hits[0][0].content)
            return

        created = await store.create_memory(
            session=session,
            client=client,
            user_id=user.id,
            content=validation.sanitized_content,
            category=op.category or MemoryCategory.GENERAL.value,
            source_type=MemorySourceType.EXPLICIT_CHAT.value,
            confidence=1.0,
            source_conversation_id=conversation_id,
            source_message_id=message_id,
        )
        owned_by_id[str(created.id)] = created
        result.actions_performed.append("ADD")
        result.saved.append(store.serialize_memory(created))
        return

    if op.op in (MemoryOpType.UPDATE, MemoryOpType.SUPERSEDE):
        candidate = (op.content or "").strip()
        validation = validate_memory_content(candidate, is_explicit=True)
        if not validation.allowed:
            result.actions_performed.append("REJECTED")
            result.rejected.append({
                "content": candidate[:120],
                "reason": validation.rejection_reason or "Rejected by memory privacy policy.",
            })
            return

        target: Optional[UserMemory] = next((owned_by_id[i] for i in op.target_memory_ids if i in owned_by_id), None)
        if target is None:
            sem_hits = await store.semantic_search(
                session=session,
                client=client,
                user_id=user.id,
                query=validation.sanitized_content,
                limit=1,
                threshold=0.50,
            )
            if sem_hits:
                target = sem_hits[0][0]

        if target is not None:
            if op.op == MemoryOpType.SUPERSEDE:
                pair = await store.supersede_memory(
                    session=session,
                    client=client,
                    user_id=user.id,
                    old_memory_id=target.id,
                    new_content=validation.sanitized_content,
                    category=op.category or target.category,
                    source_type=MemorySourceType.EXPLICIT_CHAT.value,
                    confidence=1.0,
                    source_conversation_id=conversation_id,
                    source_message_id=message_id,
                )
                if pair:
                    old_m, new_m = pair
                    owned_by_id.pop(str(old_m.id), None)
                    owned_by_id[str(new_m.id)] = new_m
                    serialized = store.serialize_memory(new_m)
                    serialized["previous_text"] = old_m.content
                    result.actions_performed.append("UPDATE")
                    result.updated.append(serialized)
                    return
            else:
                old_text = target.content
                updated = await store.update_memory(
                    session=session,
                    client=client,
                    user_id=user.id,
                    memory_id=target.id,
                    new_content=validation.sanitized_content,
                    category=op.category or target.category,
                )
                if updated:
                    owned_by_id[str(updated.id)] = updated
                    serialized = store.serialize_memory(updated)
                    serialized["previous_text"] = old_text
                    result.actions_performed.append("UPDATE")
                    result.updated.append(serialized)
                    return

        created = await store.create_memory(
            session=session,
            client=client,
            user_id=user.id,
            content=validation.sanitized_content,
            category=op.category or MemoryCategory.GENERAL.value,
            source_type=MemorySourceType.EXPLICIT_CHAT.value,
            confidence=1.0,
            source_conversation_id=conversation_id,
            source_message_id=message_id,
        )
        owned_by_id[str(created.id)] = created
        result.actions_performed.append("ADD")
        result.saved.append(store.serialize_memory(created))
        return

    if op.op == MemoryOpType.DELETE:
        result.actions_performed.append("DELETE")
        if op.delete_all:
            deleted_rows = await store.bulk_delete_memories(session=session, client=client, user_id=user.id, delete_all=True)
        else:
            valid_uuids = [owned_by_id[raw_id].id for raw_id in op.target_memory_ids if raw_id in owned_by_id]
            if valid_uuids:
                deleted_rows = await store.bulk_delete_memories(session=session, client=client, user_id=user.id, memory_ids=valid_uuids)
            elif op.category:
                deleted_rows = await store.bulk_delete_memories(session=session, client=client, user_id=user.id, category=op.category)
            elif op.search_query:
                hits = await store.semantic_search(session=session, client=client, user_id=user.id, query=op.search_query, limit=10, threshold=0.48)
                deleted_rows = await store.bulk_delete_memories(session=session, client=client, user_id=user.id, memory_ids=[m.id for m, _ in hits])
            else:
                deleted_rows = []

        for m in deleted_rows:
            owned_by_id.pop(str(m.id), None)
            result.deleted.append(store.serialize_memory(m))
