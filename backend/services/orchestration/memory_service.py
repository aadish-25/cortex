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

    async def prepare_context_window(
        self,
        session: AsyncSession,
        conversation_id: uuid.UUID,
        user: User,
    ) -> Tuple[List[ChatMessage], bool]:
        """
        Constructs context window with 8,000-token rolling buffer compaction (Decisions D28 & D37).
        Uses lazy compaction:
          1. If total tokens <= 8,000, returns raw history directly (Zero LLM calls).
          2. If a cached summary already exists on the Conversation row, it is prepended
             to recent turns without re-summarizing on every turn.
          3. Only when total uncompressed context crosses 8,000 tokens is a one-off
             summarization executed and saved to `Conversation.summary`.
          4. PostgreSQL `messages` table remains 100% append-only and immutable.
        """
        # Fetch conversation record to check for cached summary
        conv_stmt = select(Conversation).where(Conversation.id == conversation_id)
        conv_res = await session.execute(conv_stmt)
        conversation = conv_res.scalar_one_or_none()
        cached_summary = conversation.summary if conversation else None

        history = await self.get_conversation_history(session, conversation_id, limit=50)
        if not history:
            return [], False

        # If a cached summary already exists, context window is summary + last RAW_PRESERVED_TURNS
        if cached_summary:
            compacted_system_msg = ChatMessage(
                role=ChatRole.SYSTEM,
                content=f"[Executive Context Summary of Past Turns]:\n{cached_summary.strip()}",
            )
            recent_turns = history[-RAW_PRESERVED_TURNS:]
            combined_tokens = estimate_token_count(cached_summary) + sum(estimate_token_count(m.content) for m in recent_turns)
            is_near = combined_tokens >= (MAX_CONVERSATION_TOKENS * 0.85)

            # If combined context is comfortably within 8,000 tokens, return immediately without re-summarizing
            if combined_tokens <= MAX_CONVERSATION_TOKENS:
                return [compacted_system_msg] + recent_turns, is_near

        total_tokens = sum(estimate_token_count(msg.content) for msg in history)
        is_near_ceiling = total_tokens >= (MAX_CONVERSATION_TOKENS * 0.85)

        # If within comfortable token limits or history is short, return intact (Zero LLM calls)
        if total_tokens <= MAX_CONVERSATION_TOKENS or len(history) <= RAW_PRESERVED_TURNS:
            return history, is_near_ceiling

        # Rolling compaction: Summarize older turns, preserve last RAW_PRESERVED_TURNS
        older_turns = history[:-RAW_PRESERVED_TURNS]
        recent_turns = history[-RAW_PRESERVED_TURNS:]

        older_text = "\n".join(f"{t.role.value}: {t.content}" for t in older_turns)
        summary_prompt = (
            "Summarize the key facts, decisions, and constraints from these earlier conversational turns "
            "into a dense, factual bullet-point executive brief. Avoid conversational fluff:\n\n"
            f"{older_text}"
        )

        try:
            summary = await llm_service.generate_text(
                prompt=summary_prompt,
                system_prompt="You are Cortex's Conversational Memory Summarizer. Extract essential context.",
                model=settings.FAST_ROUTING_MODEL,
                temperature=0.0,
            )
            compacted_message = ChatMessage(
                role=ChatRole.SYSTEM,
                content=f"[Executive Context Summary of Past Turns]:\n{summary.strip()}",
            )
            logger.info(
                "Compacted conversation %s: %d older turns summarized into %d tokens",
                conversation_id,
                len(older_turns),
                estimate_token_count(summary),
            )

            # Cache the summary on Conversation model for future turns
            if conversation:
                conversation.summary = summary.strip()
                session.add(conversation)
                await session.commit()

            return [compacted_message] + recent_turns, is_near_ceiling
        except Exception as e:
            logger.warning("Rolling summarization failed (%s); falling back to recent turns only", e)
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
