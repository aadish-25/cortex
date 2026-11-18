from datetime import datetime, timezone
import json
import logging
from typing import Any, Dict, List, Optional, Tuple
import uuid

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select, col

from backend.core.config import settings
from backend.models.chat import (
    ChatMessage,
    ChatRole,
    Conversation,
    Message,
)
from backend.models.user import User
from backend.services.orchestration.llm_service import llm_service

logger = logging.getLogger(__name__)

# Maximum active conversational context before rolling summarization triggers (Decisions D28 & D37)
MAX_CONVERSATION_TOKENS = settings.MAX_CONVERSATION_TOKENS

# Keep at least the most recent raw messages intact during compaction
RAW_PRESERVED_TURNS = 4

# Target fraction of MAX_CONVERSATION_TOKENS to retain as raw tail messages after compaction,
# leaving ~55% headroom so the next several turns do not immediately re-trigger compaction.
POST_COMPACTION_RAW_RATIO = 0.45


def _normalize_utc(dt: datetime) -> datetime:
    """Normalizes naive (SQLite) and aware (PostgreSQL) datetimes to UTC for safe comparison."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


_tiktoken_encoder: Any = None
_tiktoken_attempted: bool = False


def get_token_encoder() -> Any:
    global _tiktoken_encoder, _tiktoken_attempted
    if not _tiktoken_attempted:
        _tiktoken_attempted = True
        try:
            import tiktoken
            _tiktoken_encoder = tiktoken.get_encoding("cl100k_base")
        except Exception:
            _tiktoken_encoder = None
    return _tiktoken_encoder


def estimate_token_count(text: str) -> int:
    """Exact token counting via tiktoken (cl100k_base) with fast heuristic fallback."""
    if not text:
        return 0
    enc: Any = get_token_encoder()
    if enc is not None:
        try:
            return len(enc.encode(text, disallowed_special=()))
        except Exception:
            pass
    return max(1, int(len(text) / 3.8))


class MemoryService:
    """
    Conversational Memory & Context Manager (Phase 5.1 / Decision D28).
    Handles:
      1. Loading past conversation turns from PostgreSQL.
      2. Injecting authenticated employee profile facts (Long-Term Memory).
      3. 4,000-token rolling buffer with LLM executive compaction.
      4. Auto-generating concise conversation titles from Turn 1.
    """

    async def get_or_create_conversation(
        self,
        session: AsyncSession,
        user_id: uuid.UUID,
        conversation_id: Optional[uuid.UUID] = None,
        title: Optional[str] = None,
    ) -> Conversation:
        """Retrieves an existing conversation or provisions a new one."""
        if conversation_id:
            stmt = select(Conversation).where(
                (Conversation.id == conversation_id) & (Conversation.user_id == user_id)
            )
            res = await session.execute(stmt)
            conv = res.scalar_one_or_none()
            if conv:
                return conv
            raise ValueError("Conversation not found or access denied")

        # Provision new conversation
        new_conv = Conversation(
            id=conversation_id or uuid.uuid4(),
            user_id=user_id,
            title=title or "New Conversation",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        session.add(new_conv)
        await session.commit()
        await session.refresh(new_conv)
        logger.info("Created new Conversation %s for user %s", new_conv.id, user_id)
        return new_conv

    async def append_message(
        self,
        session: AsyncSession,
        conversation_id: uuid.UUID,
        sender: str,
        content: str,
        citations: Optional[Any] = None,
        message_id: Optional[uuid.UUID] = None,
        parent_message_id: Optional[uuid.UUID] = None,
        variant_index: int = 1,
        is_complete: bool = True,
    ) -> Message:
        """Persists a new user or assistant message to the database."""
        cites_json = json.dumps(citations) if citations else None
        msg = Message(
            id=message_id or uuid.uuid4(),
            conversation_id=conversation_id,
            sender=sender,
            content=content,
            citations=cites_json,
            parent_message_id=parent_message_id,
            variant_index=variant_index,
            is_complete=is_complete,
            created_at=datetime.now(timezone.utc),
        )
        session.add(msg)

        # Update conversation updated_at timestamp
        conv_stmt = select(Conversation).where(Conversation.id == conversation_id)
        conv_res = await session.execute(conv_stmt)
        conv = conv_res.scalar_one_or_none()
        if conv:
            conv.updated_at = datetime.now(timezone.utc)
            session.add(conv)

        await session.commit()
        await session.refresh(msg)
        return msg

    async def get_conversation_history(
        self,
        session: AsyncSession,
        conversation_id: uuid.UUID,
        limit: int = 50,
    ) -> List[ChatMessage]:
        """
        Retrieves the most recent chronological dialogue turns formatted as ChatMessage models.
        Queries descending to capture the latest messages, then reverses to chronological order.
        """
        stmt = (
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(col(Message.created_at).desc())
            .limit(limit)
        )
        res = await session.execute(stmt)
        messages = list(reversed(res.scalars().all()))

        history: List[ChatMessage] = []
        for m in messages:
            role = ChatRole.USER if m.sender == "user" else ChatRole.ASSISTANT
            history.append(
                ChatMessage(
                    role=role,
                    content=m.content,
                    timestamp=m.created_at,
                )
            )
        return history

    def build_user_context_block(self, user: User) -> str:
        """
        Formats persistent authenticated user profile facts for system prompt injection.
        Guarantees the LLM knows the user's role, department, and clearance level.
        """
        return (
            "Authenticated Employee Profile:\n"
            f"- Name: {user.name}\n"
            f"- Email: {user.email}\n"
            f"- Department: {user.department}\n"
            f"- Role: {user.role.value if hasattr(user.role, 'value') else user.role}\n"
            f"- Clearance Level: {user.clearance_level.value if hasattr(user.clearance_level, 'value') else user.clearance_level}"
        )

    async def _load_active_window_state(
        self,
        session: AsyncSession,
        conversation_id: uuid.UUID,
    ) -> Tuple[Optional[Conversation], Optional[str], List[ChatMessage], int, bool]:
        """
        Loads conversation watermark state and splits messages into:
          - cached_summary (if previously compacted)
          - unsummarized_messages (all messages strictly after `summarized_until_at`,
            or all messages if no summary exists yet, or legacy tail if summary exists without watermark)
          - active_tokens (summary_tokens + unsummarized_tokens)
          - requires_compaction (True when active_tokens > MAX_CONVERSATION_TOKENS and enough turns exist)
        """
        conv_stmt = select(Conversation).where(Conversation.id == conversation_id)
        conv_res = await session.execute(conv_stmt)
        conversation = conv_res.scalar_one_or_none()
        cached_summary = conversation.summary.strip() if (conversation and conversation.summary) else None
        watermark = conversation.summarized_until_at if conversation else None

        history = await self.get_conversation_history(session, conversation_id, limit=200)
        if not history:
            return conversation, cached_summary, [], 0, False

        if cached_summary and watermark is not None:
            wm_utc = _normalize_utc(watermark)
            unsummarized = [m for m in history if _normalize_utc(m.timestamp) > wm_utc]
            if not unsummarized:
                unsummarized = history[-RAW_PRESERVED_TURNS:]
        elif cached_summary and watermark is None:
            # Legacy summary created before `summarized_until_at` existed: preserve recent tail
            unsummarized = history[-RAW_PRESERVED_TURNS:]
        else:
            unsummarized = history

        summary_tokens = estimate_token_count(cached_summary) if cached_summary else 0
        unsummarized_tokens = sum(estimate_token_count(m.content) for m in unsummarized)
        active_tokens = summary_tokens + unsummarized_tokens
        requires_compaction = (
            active_tokens > MAX_CONVERSATION_TOKENS
            and len(unsummarized) > RAW_PRESERVED_TURNS
        )
        return conversation, cached_summary, unsummarized, active_tokens, requires_compaction

    async def needs_compaction(
        self,
        session: AsyncSession,
        conversation_id: uuid.UUID,
    ) -> bool:
        """Fast DB-only pre-check so ChatService can emit a live 'Compacting' SSE event before LLM compaction starts."""
        _, _, _, _, requires_compaction = await self._load_active_window_state(session, conversation_id)
        return requires_compaction

    async def prepare_context_window(
        self,
        session: AsyncSession,
        conversation_id: uuid.UUID,
        user: User,
    ) -> Tuple[List[ChatMessage], bool]:
        """
        Constructs a ChatGPT-style watermarked context window (Decisions D28 & D37).

        Strategy:
          - No summary yet & total tokens <= 8,000: return ALL raw history verbatim (zero LLM calls).
          - Summary cached & (summary + all unsummarized turns since `summarized_until_at`) <= 8,000:
            return [summary_msg] + ALL unsummarized turns verbatim (zero LLM calls).
          - Active tokens > 8,000: fold [previous summary if any] + older unsummarized turns into an
            updated executive brief, advance `Conversation.summarized_until_at` watermark, and keep
            the most recent raw turns intact (~45% of budget, min RAW_PRESERVED_TURNS).
          - PostgreSQL `messages` table remains 100% append-only and immutable.
        """
        conversation, cached_summary, unsummarized, active_tokens, requires_compaction = (
            await self._load_active_window_state(session, conversation_id)
        )
        if not unsummarized:
            return [], False

        is_near_ceiling = active_tokens >= (MAX_CONVERSATION_TOKENS * 0.85)

        # Fast path: fits cleanly in budget (Zero LLM calls)
        if not requires_compaction:
            if cached_summary:
                compacted_system_msg = ChatMessage(
                    role=ChatRole.SYSTEM,
                    content=f"[Executive Context Summary of Past Turns]:\n{cached_summary}",
                )
                return [compacted_system_msg] + unsummarized, is_near_ceiling
            return unsummarized, is_near_ceiling

        # Watermarked rolling compaction: preserve newest turns (~45% of budget, at least RAW_PRESERVED_TURNS)
        tail_budget = max(500, int(MAX_CONVERSATION_TOKENS * POST_COMPACTION_RAW_RATIO))
        recent_turns: List[ChatMessage] = []
        running = 0
        for msg in reversed(unsummarized):
            t = estimate_token_count(msg.content)
            if len(recent_turns) >= RAW_PRESERVED_TURNS and (running + t > tail_budget):
                break
            recent_turns.insert(0, msg)
            running += t

        # Ensure at least 1 older turn is folded so watermark strictly advances
        if len(recent_turns) >= len(unsummarized):
            recent_turns = unsummarized[-RAW_PRESERVED_TURNS:]

        older_turns = unsummarized[: len(unsummarized) - len(recent_turns)]
        new_watermark = older_turns[-1].timestamp

        older_sections: List[str] = []
        if cached_summary:
            older_sections.append(f"[Previous Executive Summary]:\n{cached_summary}")
        older_sections.append(
            "[Additional Turns to Fold In]:\n"
            + "\n".join(f"{t.role.value}: {t.content}" for t in older_turns)
        )
        older_text = "\n\n".join(older_sections)

        summary_prompt = (
            "Produce an updated, dense, factual bullet-point executive brief capturing all key facts, "
            "decisions, entities, file names, preferences, and constraints from the prior summary (if any) "
            "and the additional conversational turns below. Avoid conversational fluff:\n\n"
            f"{older_text}"
        )

        try:
            summary = await llm_service.generate_text(
                prompt=summary_prompt,
                system_prompt="You are Cortex's Conversational Memory Compactor. Preserve essential context accurately.",
                model=settings.FAST_ROUTING_MODEL,
                temperature=0.0,
            )
            clean_summary = summary.strip()
            compacted_message = ChatMessage(
                role=ChatRole.SYSTEM,
                content=f"[Executive Context Summary of Past Turns]:\n{clean_summary}",
            )
            new_summary_tokens = estimate_token_count(clean_summary)
            logger.info(
                "Compacted conversation %s: folded %d turns into %d-token brief (watermark=%s), retaining %d raw turns (%d tokens)",
                conversation_id,
                len(older_turns),
                new_summary_tokens,
                new_watermark.isoformat(),
                len(recent_turns),
                running,
            )

            # Persist updated summary + watermark on Conversation row
            if conversation:
                conversation.summary = clean_summary
                conversation.summarized_until_at = _normalize_utc(new_watermark)
                session.add(conversation)
                await session.commit()

            post_compaction_near = (new_summary_tokens + running) >= (MAX_CONVERSATION_TOKENS * 0.85)
            return [compacted_message] + recent_turns, post_compaction_near
        except Exception as e:
            logger.warning("Rolling compaction failed (%s); falling back to preserved recent turns", e)
            if cached_summary:
                compacted_system_msg = ChatMessage(
                    role=ChatRole.SYSTEM,
                    content=f"[Executive Context Summary of Past Turns]:\n{cached_summary}",
                )
                return [compacted_system_msg] + recent_turns, is_near_ceiling
            return recent_turns, is_near_ceiling

    async def auto_title_conversation_if_needed(
        self,
        session: AsyncSession,
        conversation: Conversation,
        first_user_prompt: str,
    ) -> None:
        """
        Automatically generates a 3-5 word descriptive title on Turn 1
        instead of generic 'New Conversation'.
        """
        if conversation.title and conversation.title != "New Conversation":
            return

        title_prompt = (
            "Write a concise, professional 3 to 5 word title for a conversation starting with this user message. "
            "Do NOT use quotes, punctuation, or preamble:\n\n"
            f"User message: {first_user_prompt}"
        )

        try:
            raw_title = await llm_service.generate_text(
                prompt=title_prompt,
                system_prompt="You are a concise conversation title generator.",
                model=settings.FAST_ROUTING_MODEL,
                temperature=0.2,
            )
            clean_title = raw_title.strip().strip('"\'')[:40]
            if clean_title:
                conversation.title = clean_title
                session.add(conversation)
                await session.commit()
                logger.info("Auto-titled conversation %s: '%s'", conversation.id, clean_title)
        except Exception as e:
            logger.warning("Auto-titling failed: %s", e)


# Global singleton instance
memory_service = MemoryService()
