from dataclasses import dataclass, field
import logging
import re
from typing import Any, Dict, List, Optional, Tuple
import uuid

from qdrant_client import QdrantClient
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.config import settings
from backend.db.database import async_session
from backend.models.memory import (
    MemoryCategory,
    MemorySourceType,
    MemoryStatus,
    UserMemory,
    normalize_memory_category,
)
from backend.models.user import User
from backend.services.memory.command_detector import (
    detect_explicit_memory_command,
    has_broad_personal_cue,
    is_trivial_message,
    should_attempt_background_extraction,
)
from backend.services.memory.memory_store import MemoryStore, memory_store
from backend.services.memory.privacy_guard import validate_memory_content
from backend.services.memory.prompts import (
    AUTO_RECONCILE_SYSTEM_PROMPT_V1,
    EXPLICIT_COMMAND_SYSTEM_PROMPT_V1,
    ExplicitMemoryCommandPlan,
    MemoryOpType,
    MemoryReconciliationPlan,
    PlannedMemoryOperation,
    ReconcileActionType,
)
from backend.services.orchestration.llm_service import llm_service

logger = logging.getLogger(__name__)

# High threshold only for true duplicate suppression (updates are handled by conflict reconciliation)
EXACT_DUPLICATE_THRESHOLD = 0.88
# Semantic search threshold for on-demand retrieval (local ONNX BGE-small cosine)
MEMORY_RETRIEVAL_THRESHOLD = 0.52


@dataclass
class MemoryCommandResult:
    handled: bool = False
    is_pure_memory_command: bool = False
    remaining_user_query: Optional[str] = None
    actions_performed: List[str] = field(default_factory=list)
    saved: List[Dict[str, Any]] = field(default_factory=list)
    updated: List[Dict[str, Any]] = field(default_factory=list)
    already_existed: List[str] = field(default_factory=list)
    deleted: List[Dict[str, Any]] = field(default_factory=list)
    retrieved: List[Dict[str, Any]] = field(default_factory=list)
    rejected: List[Dict[str, str]] = field(default_factory=list)
    failed: List[str] = field(default_factory=list)
    clarification_question: Optional[str] = None
    factual_summary: str = ""

    def has_mutations(self) -> bool:
        return bool(self.saved or self.updated or self.deleted or self.rejected)

    def to_sse_payload(self) -> Optional[Dict[str, Any]]:
        if not self.handled:
            return None
        if not (self.saved or self.updated or self.deleted or self.rejected or self.retrieved):
            return None
        return {
            "actions": list(dict.fromkeys(self.actions_performed)),
            "saved": [{"memory_id": m["memory_id"], "text": m["text"], "category": m["category"]} for m in self.saved],
            "updated": [{"memory_id": m["memory_id"], "text": m["text"], "category": m["category"]} for m in self.updated],
            "deleted_count": len(self.deleted),
            "deleted": [{"memory_id": m["memory_id"], "text": m["text"], "category": m["category"]} for m in self.deleted],
            "retrieved_count": len(self.retrieved),
            "rejected": self.rejected,
        }


def _format_existing_memories(memories: List[UserMemory]) -> str:
    if not memories:
        return "(none)"
    return "\n".join(
        f"- [id={m.id}] ({m.category}, updated={m.updated_at.date().isoformat()}) {m.content}"
        for m in memories
    )


def _infer_category_from_text(text: str) -> Optional[str]:
    lower = text.lower()
    if any(k in lower for k in ("coding preference", "preference", "formatting", "concise", "style", "explanation")):
        return MemoryCategory.PREFERENCE.value
    if any(k in lower for k in ("tech stack", "technology", "database", "framework", "language", "stack")):
        return MemoryCategory.TECH_STACK.value
    if any(k in lower for k in ("project", "capstone", "final-year", "app", "platform")):
        return MemoryCategory.PROJECT_CONTEXT.value
    if any(k in lower for k in ("goal", "learning", "career", "placement")):
        return MemoryCategory.GOAL.value
    if any(k in lower for k in ("workflow", "routine", "habit")):
        return MemoryCategory.WORKFLOW.value
    if any(k in lower for k in ("location", "office", "city", "campus")):
        return MemoryCategory.LOCATION.value
    if any(k in lower for k in ("personal life", "personal", "family", "children")):
        return MemoryCategory.GENERAL.value
    return None


class MemoryManagerService:
    """
    Independent Intelligent Memory Management System for Cortex.
    Decoupled from the main DAG query planner and synthesis agent.
    Supports:
      - Explicit user-directed commands: ADD, UPDATE/SUPERSEDE, DELETE, RETRIEVE (and compound messages)
      - Zero-LLM-cost hybrid retrieval (Redis-cached preferences + fast-exit + local Qdrant + PG fallback)
      - Asynchronous post-turn extraction + conflict resolution (single background LLM call)
      - Strict user_id isolation, privacy/secret guardrails, and PostgreSQL-verified consistency
    """

    def __init__(self, store: Optional[MemoryStore] = None) -> None:
        self.store = store or memory_store

    # ------------------------------------------------------------------
    # 1. Explicit User-Directed Memory Commands
    # ------------------------------------------------------------------

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
        """
        Detects and executes explicit memory commands BEFORE the main RAG/DAG pipeline.
        Always enforces `user.id` from authenticated backend context and validates all writes.
        """
        signal = detect_explicit_memory_command(user_message)
        if not signal.is_explicit:
            return MemoryCommandResult(handled=False)

        # Fetch user's active memories from PostgreSQL so IDs & categories are authoritative
        active_memories, _ = await self.store.list_memories(
            session=session,
            user_id=user.id,
            status=MemoryStatus.ACTIVE.value,
            limit=50,
            offset=0,
        )
        owned_by_id: Dict[str, UserMemory] = {str(m.id): m for m in active_memories}

        plan: Optional[ExplicitMemoryCommandPlan] = None
        if llm_service.is_configured():
            prompt = (
                f"Detected Explicit Command Hints: {', '.join(signal.hinted_ops)}\n"
                f"Previous User Message: {recent_user_message or '(none)'}\n\n"
                f"Existing User Memories:\n{_format_existing_memories(active_memories)}\n\n"
                f"Current User Message: {user_message}"
            )
            try:
                plan = await llm_service.generate_structured(
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
            plan = await self._build_deterministic_command_plan(
                session=session,
                client=client,
                user=user,
                user_message=user_message,
                recent_user_message=recent_user_message,
                hinted_ops=signal.hinted_ops,
                active_memories=active_memories,
            )

        # If parser decided NONE and nothing actionable, let normal chat proceed
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
                await self._execute_planned_operation(
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

        result.factual_summary = self._build_factual_summary(result)
        return result

    async def _execute_planned_operation(
        self,
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
            rows, _ = await self.store.list_memories(
                session=session,
                user_id=user.id,
                category=norm_cat,
                search=op.search_query,
                status=MemoryStatus.ACTIVE.value,
                limit=50,
                offset=0,
            )
            if not rows and op.search_query:
                sem_hits = await self.store.semantic_search(
                    session=session,
                    client=client,
                    user_id=user.id,
                    query=op.search_query,
                    limit=10,
                    threshold=0.42,
                    categories=[norm_cat] if norm_cat else None,
                )
                rows = [m for m, _ in sem_hits]
            result.retrieved.extend(self.store.serialize_memory(m) for m in rows)
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

            # Duplicate guard (high threshold so distinct facts are never dropped)
            existing_hits = await self.store.semantic_search(
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

            created = await self.store.create_memory(
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
            result.saved.append(self.store.serialize_memory(created))
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

            # Resolve target strictly within authenticated user's memories
            target: Optional[UserMemory] = None
            for raw_id in op.target_memory_ids:
                if raw_id in owned_by_id:
                    target = owned_by_id[raw_id]
                    break
            if target is None:
                sem_hits = await self.store.semantic_search(
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
                    pair = await self.store.supersede_memory(
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
                        serialized = self.store.serialize_memory(new_m)
                        serialized["previous_text"] = old_m.content
                        result.actions_performed.append("UPDATE")
                        result.updated.append(serialized)
                        return
                else:
                    old_text = target.content
                    updated = await self.store.update_memory(
                        session=session,
                        client=client,
                        user_id=user.id,
                        memory_id=target.id,
                        new_content=validation.sanitized_content,
                        category=op.category or target.category,
                    )
                    if updated:
                        owned_by_id[str(updated.id)] = updated
                        serialized = self.store.serialize_memory(updated)
                        serialized["previous_text"] = old_text
                        result.actions_performed.append("UPDATE")
                        result.updated.append(serialized)
                        return

            # No matching prior memory existed — create cleanly as new active record
            created = await self.store.create_memory(
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
            result.saved.append(self.store.serialize_memory(created))
            return

        if op.op == MemoryOpType.DELETE:
            result.actions_performed.append("DELETE")
            if op.delete_all:
                deleted_rows = await self.store.bulk_delete_memories(
                    session=session,
                    client=client,
                    user_id=user.id,
                    delete_all=True,
                )
            else:
                valid_uuids: List[uuid.UUID] = []
                for raw_id in op.target_memory_ids:
                    if raw_id in owned_by_id:
                        valid_uuids.append(owned_by_id[raw_id].id)

                if valid_uuids:
                    deleted_rows = await self.store.bulk_delete_memories(
                        session=session,
                        client=client,
                        user_id=user.id,
                        memory_ids=valid_uuids,
                    )
                elif op.category:
                    deleted_rows = await self.store.bulk_delete_memories(
                        session=session,
                        client=client,
                        user_id=user.id,
                        category=op.category,
                    )
                elif op.search_query:
                    hits = await self.store.semantic_search(
                        session=session,
                        client=client,
                        user_id=user.id,
                        query=op.search_query,
                        limit=10,
                        threshold=0.48,
                    )
                    deleted_rows = await self.store.bulk_delete_memories(
                        session=session,
                        client=client,
                        user_id=user.id,
                        memory_ids=[m.id for m, _ in hits],
                    )
                else:
                    deleted_rows = []

            for m in deleted_rows:
                owned_by_id.pop(str(m.id), None)
                result.deleted.append(self.store.serialize_memory(m))
            return

    async def _build_deterministic_command_plan(
        self,
        session: AsyncSession,
        client: Optional[QdrantClient],
        user: User,
        user_message: str,
        recent_user_message: Optional[str],
        hinted_ops: List[str],
        active_memories: List[UserMemory],
    ) -> ExplicitMemoryCommandPlan:
        """Offline/fallback command parser when LLM is unavailable or fails."""
        lower = user_message.lower().strip()
        ops: List[PlannedMemoryOperation] = []

        # Detect compound message (e.g. "I switched to Postgres. Recommend a backend architecture.")
        remaining_query: Optional[str] = None
        is_pure = True
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", user_message.strip()) if s.strip()]
        if len(sentences) >= 2 and sentences[-1].endswith("?") or (
            len(sentences) >= 2 and not detect_explicit_memory_command(sentences[-1]).is_explicit
        ):
            remaining_query = " ".join(sentences[1:])
            is_pure = False

        if "DELETE" in hinted_ops:
            if re.search(r"\b(?:all\s+(?:of\s+)?my\s+(?:long[- ]term\s+)?memo?r(?:y|ies)|everything\s+(?:in\s+my\s+)?memo?r(?:y|ies))\b", lower):
                ops.append(PlannedMemoryOperation(op=MemoryOpType.DELETE, delete_all=True))
            elif re.search(r"\b(?:forget\s+this|delete\s+that|forget\s+that\s+thing)\b", lower) and len(lower.split()) <= 5:
                ops.append(
                    PlannedMemoryOperation(
                        op=MemoryOpType.CLARIFY,
                        clarification_question="Which specific memory would you like me to forget?",
                    )
                )
            else:
                inferred_cat = _infer_category_from_text(lower)
                # Keyword matching against existing active memories
                topic = re.sub(
                    r"\b(?:please|pls|forget|delete|remove|erase|wipe|clear|all|everything|you|remember|about|my|the|saved|long[- ]term|memo?r(?:y|ies)|that|i|use|related|to)\b",
                    " ",
                    lower,
                )
                topic_tokens = [t for t in re.findall(r"[a-z0-9]+", topic) if len(t) >= 3]
                matched_ids = [
                    str(m.id)
                    for m in active_memories
                    if topic_tokens and any(tok in m.content.lower() for tok in topic_tokens)
                ]
                if matched_ids:
                    ops.append(PlannedMemoryOperation(op=MemoryOpType.DELETE, target_memory_ids=matched_ids))
                elif inferred_cat:
                    ops.append(PlannedMemoryOperation(op=MemoryOpType.DELETE, category=inferred_cat))
                elif topic_tokens:
                    ops.append(PlannedMemoryOperation(op=MemoryOpType.DELETE, search_query=" ".join(topic_tokens)))
                else:
                    ops.append(
                        PlannedMemoryOperation(
                            op=MemoryOpType.CLARIFY,
                            clarification_question="Which memories would you like me to delete? You can name a topic, a category, or ask to delete all memories.",
                        )
                    )

        if "UPDATE" in hinted_ops:
            primary_text = sentences[0] if sentences else user_message
            cleaned = re.sub(
                r"^(?:please\s+)?(?:update|change|modify|correct|edit)\s+(?:my|that|the)\s+(?:saved\s+)?memo?r(?:y|ies)\s*[:,-]?\s*",
                "",
                primary_text,
                flags=re.IGNORECASE,
            ).strip()
            # Match existing memory mentioning the old term ("from X to Y" or "instead of X" or "X preference to Y")
            target_ids: List[str] = []
            swap_match = re.search(
                r"(?:instead\s+of|from)\s+([A-Za-z0-9_+.#-]+)|update\s+(?:my\s+)?([A-Za-z0-9_+.#-]+)\s+preference\s+to\s+(.+)",
                cleaned,
                re.IGNORECASE,
            )
            if swap_match:
                old_term = (swap_match.group(1) or swap_match.group(2) or "").lower()
                if swap_match.group(3):
                    cleaned = f"Prefers {swap_match.group(3).strip().rstrip('.')}."
                if old_term:
                    target_ids = [str(m.id) for m in active_memories if old_term in m.content.lower()]
            if not target_ids and active_memories:
                hits = await self.store.semantic_search(session, client, user.id, cleaned, limit=1, threshold=0.45)
                if hits:
                    target_ids = [str(hits[0][0].id)]
            ops.append(
                PlannedMemoryOperation(
                    op=MemoryOpType.SUPERSEDE if target_ids else MemoryOpType.ADD,
                    content=cleaned or primary_text,
                    category=_infer_category_from_text(cleaned) or MemoryCategory.GENERAL.value,
                    target_memory_ids=target_ids,
                )
            )

        if "ADD" in hinted_ops and "UPDATE" not in hinted_ops:
            primary_text = sentences[0] if sentences else user_message
            cleaned = re.sub(
                r"[,.;!\s]*\b(?:please\s+|pls\s+)?(?:add+\s+(?:this\s+|that\s+|it\s+)?to\s+(?:my\s+)?(?:long[- ]term\s+)?memo?r(?:y|ies)|save\s+(?:this\s+|that\s+|it\s+)?(?:to|in|as)\s+(?:my\s+|a\s+)?(?:long[- ]term\s+)?(?:memo?r(?:y|ies)|preference)|remember\s+(?:that|this)|note\s+that|store\s+(?:this|that)\s+in\s+(?:my\s+)?memo?r(?:y|ies))\b[:,.;!\s]*",
                " ",
                primary_text,
                flags=re.IGNORECASE,
            ).strip()
            if len(cleaned) < 6 and recent_user_message:
                cleaned = recent_user_message.strip()
            if cleaned:
                ops.append(
                    PlannedMemoryOperation(
                        op=MemoryOpType.ADD,
                        content=cleaned,
                        category=_infer_category_from_text(cleaned) or MemoryCategory.PREFERENCE.value,
                    )
                )

        if "RETRIEVE" in hinted_ops:
            inferred_cat = None
            if not re.search(r"\b(?:everything|all|about\s+me)\b", lower):
                inferred_cat = _infer_category_from_text(lower)
            ops.append(PlannedMemoryOperation(op=MemoryOpType.RETRIEVE, category=inferred_cat))

        return ExplicitMemoryCommandPlan(
            operations=ops,
            remaining_user_query=remaining_query,
            is_pure_memory_command=is_pure,
        )

    def _build_factual_summary(self, result: MemoryCommandResult) -> str:
        sections: List[str] = []

        if result.clarification_question:
            sections.append(result.clarification_question)

        if result.saved:
            bullets = "\n".join(f"- **[{m['category']}]** {m['text']}" for m in result.saved)
            sections.append(f"Saved to your long-term memory:\n{bullets}")

        if result.already_existed:
            bullets = "\n".join(f"- {t}" for t in result.already_existed)
            sections.append(f"Already saved in your long-term memory:\n{bullets}")

        if result.updated:
            lines = []
            for m in result.updated:
                prev = m.get("previous_text")
                if prev:
                    lines.append(f"- **[{m['category']}]** Updated from *\"{prev}\"* to **\"{m['text']}\"**")
                else:
                    lines.append(f"- **[{m['category']}]** {m['text']}")
            sections.append("Updated your long-term memory:\n" + "\n".join(lines))

        if "DELETE" in result.actions_performed:
            if result.deleted:
                bullets = "\n".join(f"- **[{m['category']}]** {m['text']}" for m in result.deleted)
                sections.append(f"Deleted {len(result.deleted)} memory item(s):\n{bullets}")
            elif not result.clarification_question and not result.failed:
                sections.append("No matching memories were found to delete.")

        if "RETRIEVE" in result.actions_performed:
            if result.retrieved:
                bullets = "\n".join(f"- **[{m['category']}]** {m['text']}" for m in result.retrieved)
                sections.append(f"Here is what I currently remember about you ({len(result.retrieved)} item(s)):\n{bullets}")
            else:
                sections.append("I don't have any matching long-term memories saved for you right now.")

        if result.rejected:
            bullets = "\n".join(f"- {r['reason']}" for r in result.rejected)
            sections.append(f"Memory policy notice:\n{bullets}")

        if result.failed:
            bullets = "\n".join(f"- {err}" for err in result.failed)
            sections.append(f"Memory operation could not be completed:\n{bullets}")

        return "\n\n".join(sections).strip()

    # ------------------------------------------------------------------
    # 2. Zero-LLM-Cost Hybrid Memory Retrieval
    # ------------------------------------------------------------------

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
        """
        Zero-LLM-cost hybrid retrieval:
          1. Always load active `preference` memories (Redis-cached, <3ms).
          2. Fast-exit (<1ms) if the user has 0 active non-preference memories.
          3. Skip vector search on tiny trivial greetings/acknowledgments.
          4. Run local ONNX FastEmbed + Qdrant search (~25ms, PG-verified active rows).
          5. Broad-personal fallback (0 LLM calls): if query uses indirect personal phrasing
             (e.g. 'suit my background', 'same stack as before') and semantic search returned <2 hits,
             pull top-5 recent active project/tech/goal memories directly from PostgreSQL.
        """
        formatted: List[str] = []
        seen_texts: set[str] = set()

        # 1. Always-on active preferences
        try:
            prefs = await self.store.get_active_preferences(session=session, user_id=user.id, limit=max_preferences)
            for p in prefs:
                if p not in seen_texts:
                    seen_texts.add(p)
                    formatted.append(f"[preference] {p}")
        except Exception as exc:
            logger.warning("Failed to load user preferences (%s); continuing", exc)

        if not query or is_trivial_message(query):
            return formatted

        # 2. Zero-memory fast-exit
        try:
            non_pref_count = await self.store.get_active_non_pref_count(session=session, user_id=user.id)
        except Exception as exc:
            logger.warning("Failed to count active user memories (%s); continuing", exc)
            return formatted

        if non_pref_count == 0:
            return formatted

        # 3. Direct local semantic search (~25ms, zero LLM tokens)
        semantic_hits = await self.store.semantic_search(
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

        # 4. Broad-personal fallback when semantic overlap may be low (still zero LLM calls)
        if len(semantic_hits) < 2 and has_broad_personal_cue(query):
            recent_personal = await self.store.get_recent_active_by_categories(
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

    # ------------------------------------------------------------------
    # 3. Asynchronous Automatic Extraction & Conflict Resolution
    # ------------------------------------------------------------------

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
        """
        Extracts durable personal facts from a normal chat turn and reconciles them
        against existing active memories in a SINGLE structured LLM call (ADD / UPDATE / SUPERSEDE / NOOP).
        Never raises on LLM or storage errors so normal chat is never impacted.
        """
        if not should_attempt_background_extraction(user_message):
            return []

        if not llm_service.is_configured():
            return []

        # Pull top-5 semantically related existing memories so the LLM can resolve updates/conflicts
        related_hits = await self.store.semantic_search(
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
            f"Existing Related Memories:\n{_format_existing_memories(related_memories)}\n\n"
            f"Current User Message: {user_message}"
        )

        try:
            plan = await llm_service.generate_structured(
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

            candidate = (action.content or "").strip()
            validation = validate_memory_content(candidate, is_explicit=False)
            if not validation.allowed:
                logger.info(
                    "Background memory skipped by privacy guard for user %s: %s",
                    user_id,
                    validation.rejection_reason,
                )
                continue

            try:
                if action.action == ReconcileActionType.SUPERSEDE and action.target_memory_id in owned_by_id:
                    target = owned_by_id[action.target_memory_id]
                    pair = await self.store.supersede_memory(
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
                    updated = await self.store.update_memory(
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
                    # ADD with strict near-exact duplicate guard
                    dup_hits = await self.store.semantic_search(
                        session=session,
                        client=client,
                        user_id=user_id,
                        query=validation.sanitized_content,
                        limit=1,
                        threshold=EXACT_DUPLICATE_THRESHOLD,
                    )
                    if dup_hits:
                        continue
                    created = await self.store.create_memory(
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
        """
        Safe fire-and-forget entrypoint for post-turn background extraction.
        Opens its own isolated AsyncSession so it never shares a closed request session.
        """
        try:
            async with async_session() as bg_session:
                await self.extract_and_reconcile(
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


memory_manager = MemoryManagerService()
