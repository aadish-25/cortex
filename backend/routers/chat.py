from datetime import datetime, timezone
import json
import logging
from typing import Any, Dict, List, Optional
import uuid
from fastapi import APIRouter, Depends, Header, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from qdrant_client import QdrantClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from backend.core.config import settings
from backend.core.security import get_current_user
from backend.db.database import get_db
from backend.db.qdrant import get_qdrant_client
from backend.models.chat import (
    Conversation,
    Message,
    MessageFeedback,
)
from backend.models.enums import FeedbackCategory
from backend.models.user import User
from backend.services.orchestration.chat_service import chat_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/chat", tags=["Chat"])


# ==========================================
# Request & Response Schemas
# ==========================================

class ChatMessageRequest(BaseModel):
    message: str = Field(..., min_length=1, description="Raw user query text")
    conversation_id: Optional[uuid.UUID] = Field(default=None, description="Existing conversation session ID")
    selected_model: Optional[str] = Field(default=None, description="Dynamic model selection override (Decision D30)")


class FeedbackRequest(BaseModel):
    message_id: uuid.UUID = Field(..., description="ID of assistant message being evaluated")
    is_thumbs_up: bool = Field(..., description="True for positive feedback, False for negative")
    category: Optional[FeedbackCategory] = Field(default=None, description="Feedback issue category")
    comment: Optional[str] = Field(default=None, max_length=1000, description="Optional employee feedback comment")


class ConversationRead(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    title: str
    summary: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class MessageRead(BaseModel):
    id: uuid.UUID
    conversation_id: uuid.UUID
    sender: str
    content: str
    citations: Optional[List[str]] = None
    variant_index: int
    created_at: datetime


class ModelInfo(BaseModel):
    id: str
    name: str
    provider: str
    context_window: int
    is_free: bool
    badge: str
    description: str


# ==========================================
# Endpoints
# ==========================================

@router.post("/message")
async def send_chat_message(
    payload: ChatMessageRequest,
    last_event_id: Optional[str] = Header(None, alias="Last-Event-ID"),
    db_session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Primary Streaming Conversational Endpoint (Decision D17).
    Returns real-time Server-Sent Events (SSE) emitting:
      - 'step': Execution progress notifications
      - 'token': Incremental streaming text chunks
      - 'citations': Verified document source citations
      - 'action_card': HITL ActionDraft if ticket proposed
      - 'suggestions': Exactly 2 dynamic follow-up prompt pills
      - 'done': Final message ID and conversation ID
    Supports reconnection replay from Redis Stream buffer via 'Last-Event-ID'.
    """
    client = get_qdrant_client()

    stream_generator = chat_service.stream_chat_turn(
        session=db_session,
        client=client,
        user=current_user,
        user_message=payload.message,
        conversation_id=payload.conversation_id,
        selected_model=payload.selected_model,
        last_event_id=last_event_id,
    )

    return StreamingResponse(
        stream_generator,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/conversations", response_model=List[ConversationRead])
async def list_user_conversations(
    limit: int = 50,
    db_session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Lists all conversation sessions owned by the authenticated employee."""
    stmt = (
        select(Conversation)
        .where(Conversation.user_id == current_user.id)
        .order_by(col(Conversation.updated_at).desc())
        .limit(limit)
    )
    res = await db_session.execute(stmt)
    return res.scalars().all()


@router.get("/conversations/{conversation_id}/messages", response_model=List[MessageRead])
async def get_conversation_messages(
    conversation_id: uuid.UUID,
    db_session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Retrieves all verbatim dialogue turns from the immutable PostgreSQL messages ledger.
    Guarantees privacy: employees can only inspect conversations they own.
    """
    conv_stmt = select(Conversation).where(
        (Conversation.id == conversation_id) & (Conversation.user_id == current_user.id)
    )
    conv_res = await db_session.execute(conv_stmt)
    conv = conv_res.scalar_one_or_none()
    if not conv:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found or access denied",
        )

    stmt = (
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(col(Message.created_at).asc())
    )
    res = await db_session.execute(stmt)
    messages = res.scalars().all()

    results: List[MessageRead] = []
    for m in messages:
        cites: Optional[List[str]] = None
        if m.citations:
            try:
                cites = json.loads(m.citations)
            except Exception:
                cites = [m.citations]
        results.append(
            MessageRead(
                id=m.id,
                conversation_id=m.conversation_id,
                sender=m.sender,
                content=m.content,
                citations=cites,
                variant_index=m.variant_index,
                created_at=m.created_at,
            )
        )
    return results


@router.post("/feedback", status_code=status.HTTP_201_CREATED)
async def submit_message_feedback(
    payload: FeedbackRequest,
    db_session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Persists user thumbs up / down evaluation and optional comments for quality audits.
    """
    # Verify target message exists
    msg_stmt = select(Message).where(Message.id == payload.message_id)
    msg_res = await db_session.execute(msg_stmt)
    msg = msg_res.scalar_one_or_none()
    if not msg:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Target message not found",
        )

    feedback = MessageFeedback(
        id=uuid.uuid4(),
        message_id=payload.message_id,
        user_id=current_user.id,
        is_thumbs_up=payload.is_thumbs_up,
        category=payload.category,
        comment=payload.comment,
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(feedback)
    await db_session.commit()
    await db_session.refresh(feedback)
    return {
        "status": "success",
        "feedback_id": str(feedback.id),
        "message": "Feedback recorded successfully",
    }


@router.get("/available-models", response_model=List[ModelInfo])
async def list_available_models(
    current_user: User = Depends(get_current_user),
):
    """
    Exposes supported foundation and reasoning models for the UI model picker (Decision D30).
    Categorized by provider, context metrics, and ':free' transparency badge.
    """
    return [
        ModelInfo(
            id="groq/qwen/qwen3.8-27b",
            name="Qwen 2.5 27B (Groq Fast)",
            provider="Groq",
            context_window=32768,
            is_free=True,
            badge=":free",
            description="Ultra low latency inference on LPUs (<1s response time)",
        ),
        ModelInfo(
            id="groq/llama-3.3-70b-versatile",
            name="Llama 3.3 70B (Groq)",
            provider="Groq",
            context_window=131072,
            is_free=True,
            badge=":free",
            description="High-reasoning general assistant with 128k context window",
        ),
        ModelInfo(
            id="openrouter/meta-llama/llama-3.3-70b-instruct:free",
            name="Llama 3.3 70B Instruct",
            provider="OpenRouter",
            context_window=131072,
            is_free=True,
            badge=":free",
            description="High-capacity open model via OpenRouter Free Tier",
        ),
        ModelInfo(
            id="openrouter/deepseek/deepseek-r1:free",
            name="DeepSeek R1",
            provider="OpenRouter",
            context_window=65536,
            is_free=True,
            badge=":free",
            description="Autonomous chain-of-thought mathematical & logical reasoning",
        ),
        ModelInfo(
            id="gemini/gemini-2.5-flash",
            name="Gemini 2.5 Flash",
            provider="Gemini",
            context_window=1048576,
            is_free=True,
            badge=":free",
            description="Google AI Studio 1M context multi-modal fallback engine",
        ),
    ]
