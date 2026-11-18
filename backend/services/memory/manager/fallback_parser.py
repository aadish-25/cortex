import re
from typing import List, Optional

from qdrant_client import QdrantClient
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.memory import MemoryCategory, UserMemory
from backend.models.user import User
from backend.services.memory.command_detector import detect_explicit_memory_command
from backend.services.memory.prompts import (
    ExplicitMemoryCommandPlan,
    MemoryOpType,
    PlannedMemoryOperation,
)
from backend.services.memory.store.repository import MemoryStore


def infer_category_from_text(text: str) -> Optional[str]:
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


async def build_deterministic_command_plan(
    store: MemoryStore,
    session: AsyncSession,
    client: Optional[QdrantClient],
    user: User,
    user_message: str,
    recent_user_message: Optional[str],
    hinted_ops: List[str],
    active_memories: List[UserMemory],
) -> ExplicitMemoryCommandPlan:
    """Offline/fallback explicit command parser when LLM is unavailable or fails."""
    lower = user_message.lower().strip()
    ops: List[PlannedMemoryOperation] = []

    remaining_query: Optional[str] = None
    is_pure = True
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", user_message.strip()) if s.strip()]
    if (len(sentences) >= 2 and sentences[-1].endswith("?")) or (
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
            inferred_cat = infer_category_from_text(lower)
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
            hits = await store.semantic_search(session, client, user.id, cleaned, limit=1, threshold=0.45)
            if hits:
                target_ids = [str(hits[0][0].id)]
        ops.append(
            PlannedMemoryOperation(
                op=MemoryOpType.SUPERSEDE if target_ids else MemoryOpType.ADD,
                content=cleaned or primary_text,
                category=infer_category_from_text(cleaned) or MemoryCategory.GENERAL.value,
                target_memory_ids=target_ids,
            )
        )

    if "ADD" in hinted_ops and "UPDATE" not in hinted_ops:
        primary_text = sentences[0] if sentences else user_message
        cleaned = re.sub(
            r"[,.;!\s]*\b(?:please\s+|pls\s+)?(?:add+\s+(?:this\s+|that\s+|it\s+)?to\s+(?:my\s+)?(?:long[- ]term\s+)?(?:memo?r(?:y|ies)|(?:[\w-]+\s+)?preferences?)|save\s+(?:this\s+|that\s+|it\s+)?(?:to|in|as)\s+(?:my\s+|a\s+)?(?:long[- ]term\s+)?(?:memo?r(?:y|ies)|(?:[\w-]+\s+)?preferences?)|remember\s+(?:that|this)|note\s+that|store\s+(?:this|that)\s+in\s+(?:my\s+)?memo?r(?:y|ies))\b[:,.;!\s]*",
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
                    category=infer_category_from_text(cleaned) or MemoryCategory.PREFERENCE.value,
                )
            )

    if "RETRIEVE" in hinted_ops:
        inferred_cat = None
        if not re.search(r"\b(?:everything|all|about\s+me)\b", lower):
            inferred_cat = infer_category_from_text(lower)
        ops.append(PlannedMemoryOperation(op=MemoryOpType.RETRIEVE, category=inferred_cat))

    return ExplicitMemoryCommandPlan(
        operations=ops,
        remaining_user_query=remaining_query,
        is_pure_memory_command=is_pure,
    )
