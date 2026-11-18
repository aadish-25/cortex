from datetime import datetime, timezone
import json
import logging
from typing import Any, Dict, List, Optional
import uuid
from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from qdrant_client import QdrantClient
from sqlalchemy import func
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
from backend.models.pagination import PaginatedResponse
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
    message_id: Optional[uuid.UUID] = Field(
        default=None,
        description="F9: Client-supplied message_id for SSE reconnection. Must match the original stream's message_id to replay from the correct Redis buffer."
    )


class RegenerateRequest(BaseModel):
    message_id: uuid.UUID = Field(..., description="New assistant message ID used for SSE replay")
    selected_model: Optional[str] = None


class FeedbackRequest(BaseModel):
    message_id: uuid.UUID = Field(..., description="ID of assistant message being evaluated")
    is_thumbs_up: bool = Field(..., description="True for positive feedback, False for negative")
    category: Optional[FeedbackCategory] = Field(default=None, description="Feedback issue category")
    comment: Optional[str] = Field(default=None, max_length=1000, description="Optional employee feedback comment")


class ConversationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

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
    citations: Optional[Any] = None
    variant_index: int
    parent_message_id: Optional[uuid.UUID] = None
    is_complete: bool = True
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
    if payload.conversation_id:
        owner_result = await db_session.execute(
            select(Conversation).where(
                Conversation.id == payload.conversation_id,
                Conversation.user_id == current_user.id,
            )
        )
        if owner_result.scalar_one_or_none() is None:
            raise HTTPException(status_code=404, detail="Conversation not found")

    stream_generator = chat_service.stream_chat_turn(
        session=db_session,
        client=client,
        user=current_user,
        user_message=payload.message,
        conversation_id=payload.conversation_id,
        selected_model=payload.selected_model,
        message_id=payload.message_id,
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


@router.get("/conversations", response_model=PaginatedResponse[ConversationRead])
async def list_user_conversations(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=30, ge=1, le=100),
    search: Optional[str] = Query(default=None, max_length=160),
    db_session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Lists the authenticated user's conversations using offset pagination."""
    conditions = [Conversation.user_id == current_user.id]
    if search:
        conditions.append(Conversation.title.ilike(f"%{search.strip()}%"))
    total = int((await db_session.execute(select(func.count(Conversation.id)).where(*conditions))).scalar_one() or 0)
    stmt = (
        select(Conversation)
        .where(*conditions)
        .order_by(col(Conversation.updated_at).desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await db_session.execute(stmt)
    return PaginatedResponse[ConversationRead](
        items=[ConversationRead.model_validate(item) for item in result.scalars().all()],
        page=page,
        page_size=page_size,
        total=total,
        total_pages=(total + page_size - 1) // page_size,
    )


@router.get("/conversations/{conversation_id}/messages", response_model=PaginatedResponse[MessageRead])
async def get_conversation_messages(
    conversation_id: uuid.UUID,
    page: int = Query(default=1, ge=1, description="Page 1 contains the newest messages"),
    page_size: int = Query(default=40, ge=1, le=100),
    db_session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Returns a chronological page of messages; page 1 is the newest slice."""
    conv_stmt = select(Conversation).where(
        (Conversation.id == conversation_id) & (Conversation.user_id == current_user.id)
    )
    conv_res = await db_session.execute(conv_stmt)
    if not conv_res.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found or access denied")

    total = int((await db_session.execute(
        select(func.count(Message.id)).where(Message.conversation_id == conversation_id)
    )).scalar_one() or 0)
    stmt = (
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(col(Message.created_at).desc(), col(Message.id).desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    res = await db_session.execute(stmt)
    messages = list(reversed(res.scalars().all()))

    results: List[MessageRead] = []
    for m in messages:
        cites: Optional[List[str]] = None
        if m.citations:
            try:
                cites = json.loads(m.citations)
            except Exception:
                cites = [m.citations]
        results.append(MessageRead(
            id=m.id,
            conversation_id=m.conversation_id,
            sender=m.sender,
            content=m.content,
            citations=cites,
            variant_index=m.variant_index,
            parent_message_id=m.parent_message_id,
            is_complete=m.is_complete,
            created_at=m.created_at,
        ))
    return PaginatedResponse[MessageRead](
        items=results,
        page=page,
        page_size=page_size,
        total=total,
        total_pages=(total + page_size - 1) // page_size,
    )

@router.post("/messages/{assistant_message_id}/regenerate")
async def regenerate_assistant_message(
    assistant_message_id: uuid.UUID,
    payload: RegenerateRequest,
    last_event_id: Optional[str] = Header(None, alias="Last-Event-ID"),
    db_session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Generate another assistant variant linked to the original user turn."""
    target_result = await db_session.execute(
        select(Message).where(Message.id == assistant_message_id, Message.sender == "assistant")
    )
    target = target_result.scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=404, detail="Assistant message not found")

    conversation_result = await db_session.execute(
        select(Conversation).where(
            Conversation.id == target.conversation_id,
            Conversation.user_id == current_user.id,
        )
    )
    conversation = conversation_result.scalar_one_or_none()
    if conversation is None:
        raise HTTPException(status_code=404, detail="Assistant message not found")

    parent = None
    if target.parent_message_id:
        parent_result = await db_session.execute(
            select(Message).where(
                Message.id == target.parent_message_id,
                Message.conversation_id == conversation.id,
                Message.sender == "user",
            )
        )
        parent = parent_result.scalar_one_or_none()
    if parent is None:
        parent_result = await db_session.execute(
            select(Message)
            .where(
                Message.conversation_id == conversation.id,
                Message.sender == "user",
                Message.created_at < target.created_at,
            )
            .order_by(col(Message.created_at).desc())
            .limit(1)
        )
        parent = parent_result.scalar_one_or_none()
    if parent is None:
        raise HTTPException(status_code=409, detail="Original user turn could not be resolved")

    # Backfill parent linkage for assistant messages created before variants were enabled.
    target.parent_message_id = parent.id
    target.variant_index = max(1, target.variant_index)
    db_session.add(target)
    await db_session.commit()

    variants_result = await db_session.execute(
        select(func.max(Message.variant_index)).where(
            Message.conversation_id == conversation.id,
            Message.sender == "assistant",
            Message.parent_message_id == parent.id,
        )
    )
    next_variant = int(variants_result.scalar_one() or 1) + 1

    stream_generator = chat_service.stream_chat_turn(
        session=db_session,
        client=get_qdrant_client(),
        user=current_user,
        user_message=parent.content,
        conversation_id=conversation.id,
        message_id=payload.message_id,
        selected_model=payload.selected_model,
        last_event_id=last_event_id,
        append_user_message=False,
        parent_message_id=parent.id,
        variant_index=next_variant,
    )
    return StreamingResponse(
        stream_generator,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


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
    owner_stmt = select(Conversation).where(
        Conversation.id == msg.conversation_id,
        Conversation.user_id == current_user.id,
    )
    owner_res = await db_session.execute(owner_stmt)
    if msg.sender != "assistant" or owner_res.scalar_one_or_none() is None:
        # Do not disclose whether another user's message exists.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Target message not found")

    existing_result = await db_session.execute(
        select(MessageFeedback).where(
            MessageFeedback.message_id == payload.message_id,
            MessageFeedback.user_id == current_user.id,
        )
    )
    feedback = existing_result.scalar_one_or_none()
    updated = feedback is not None
    if feedback is None:
        feedback = MessageFeedback(
            id=uuid.uuid4(),
            message_id=payload.message_id,
            user_id=current_user.id,
            is_thumbs_up=payload.is_thumbs_up,
            category=payload.category,
            comment=payload.comment,
            created_at=datetime.now(timezone.utc),
        )
    else:
        feedback.is_thumbs_up = payload.is_thumbs_up
        feedback.category = payload.category
        feedback.comment = payload.comment
    db_session.add(feedback)
    await db_session.commit()
    await db_session.refresh(feedback)
    return {
        "status": "success",
        "feedback_id": str(feedback.id),
        "updated": updated,
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


# ===========================================================================
# F5: Intelligent Memory Management Endpoints (Canonical PG + Qdrant)
# ===========================================================================

class MemoryItem(BaseModel):
    memory_id: str
    text: str
    category: str
    status: str = "active"
    confidence: Optional[float] = None
    source_type: Optional[str] = None
    supersedes_memory_id: Optional[str] = None
    version: int = 1
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    score: Optional[float] = None


class MemoryCreateRequest(BaseModel):
    text: str = Field(..., min_length=4, max_length=1000)
    category: str = Field(default="preference", max_length=60)


class MemoryUpdateRequest(BaseModel):
    text: str = Field(..., min_length=2, max_length=1000)
    category: Optional[str] = Field(default=None, max_length=60)
    expected_version: Optional[int] = Field(default=None, ge=1)


class MemoryBulkDeleteRequest(BaseModel):
    memory_ids: List[str] = Field(default_factory=list)
    category: Optional[str] = Field(default=None, max_length=60)
    delete_all: bool = False


def _safe_qdrant_client() -> Optional[QdrantClient]:
    try:
        return get_qdrant_client()
    except Exception:
        return None


@router.get("/memories")
async def list_user_memories(
    limit: int = Query(default=25, ge=1, le=100),
    cursor: Optional[str] = Query(default=None),
    category: Optional[str] = Query(default=None, max_length=60),
    search: Optional[str] = Query(default=None, max_length=200),
    status_filter: str = Query(default="active", alias="status", pattern="^(active|superseded|all)$"),
    db_session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Paginated, filterable list of the authenticated user's archival memories.
    Strictly scoped to `current_user.id` — never exposes another employee's memories.
    """
    from backend.services.memory.memory_store import memory_store

    offset = 0
    if cursor:
        try:
            offset = max(0, int(cursor))
        except ValueError:
            offset = 0

    items, total = await memory_store.list_memories(
        session=db_session,
        user_id=current_user.id,
        category=category,
        search=search,
        status=status_filter,
        limit=limit,
        offset=offset,
    )

    # If keyword search returned empty and query is meaningful, supplement with semantic search
    serialized = [MemoryItem(**memory_store.serialize_memory(m)).model_dump() for m in items]
    if search and search.strip() and not serialized and status_filter == "active":
        qclient = _safe_qdrant_client()
        sem_hits = await memory_store.semantic_search(
            session=db_session,
            client=qclient,
            user_id=current_user.id,
            query=search.strip(),
            limit=limit,
            threshold=0.42,
            categories=[category] if category else None,
        )
        serialized = [
            MemoryItem(**memory_store.serialize_memory(m, score=round(sc, 4))).model_dump()
            for m, sc in sem_hits
        ]
        total = len(serialized)

    next_offset = offset + len(items)
    next_cursor = str(next_offset) if (not search and next_offset < total) or (search and len(items) == limit and next_offset < total) else None
    return {
        "items": serialized,
        "total": total,
        "next_cursor": next_cursor,
        "has_more": next_cursor is not None,
    }


@router.get("/memories/search")
async def search_user_memories(
    q: str = Query(..., min_length=1, max_length=300),
    category: Optional[str] = Query(default=None, max_length=60),
    limit: int = Query(default=10, ge=1, le=50),
    db_session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Hybrid semantic + keyword search strictly scoped to the authenticated user."""
    from backend.services.memory.memory_store import memory_store

    qclient = _safe_qdrant_client()
    sem_hits = await memory_store.semantic_search(
        session=db_session,
        client=qclient,
        user_id=current_user.id,
        query=q.strip(),
        limit=limit,
        threshold=0.40,
        categories=[category] if category else None,
    )
    seen_ids = {m.id for m, _ in sem_hits}
    results = [MemoryItem(**memory_store.serialize_memory(m, score=round(sc, 4))).model_dump() for m, sc in sem_hits]

    kw_items, _ = await memory_store.list_memories(
        session=db_session,
        user_id=current_user.id,
        category=category,
        search=q.strip(),
        status="active",
        limit=limit,
    )
    for m in kw_items:
        if m.id not in seen_ids and len(results) < limit:
            results.append(MemoryItem(**memory_store.serialize_memory(m)).model_dump())

    return {"items": results, "total": len(results)}


@router.post("/memories", response_model=MemoryItem, status_code=status.HTTP_201_CREATED)
async def create_user_memory(
    payload: MemoryCreateRequest,
    db_session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Manually adds a validated personal memory owned by the authenticated user."""
    from backend.models.memory import MemorySourceType
    from backend.services.memory.memory_store import memory_store
    from backend.services.memory.privacy_guard import validate_memory_content

    validation = validate_memory_content(payload.text, is_explicit=True)
    if not validation.allowed:
        raise HTTPException(
            status_code=422,
            detail=validation.rejection_reason or "Memory content rejected by privacy policy.",
        )

    qclient = _safe_qdrant_client()
    created = await memory_store.create_memory(
        session=db_session,
        client=qclient,
        user_id=current_user.id,
        content=validation.sanitized_content,
        category=payload.category,
        source_type=MemorySourceType.MANUAL_UI.value,
        confidence=1.0,
    )
    return MemoryItem(**memory_store.serialize_memory(created))


@router.get("/memories/{memory_id}", response_model=MemoryItem)
async def get_user_memory_detail(
    memory_id: str,
    db_session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Retrieves a single memory owned by the authenticated user."""
    from backend.services.memory.memory_store import memory_store

    try:
        mem_uuid = uuid.UUID(memory_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Memory not found or access denied.")

    mem = await memory_store.get_memory(session=db_session, user_id=current_user.id, memory_id=mem_uuid)
    if mem is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Memory not found or access denied.")
    return MemoryItem(**memory_store.serialize_memory(mem))


@router.patch("/memories/{memory_id}", response_model=MemoryItem)
async def update_user_memory(
    memory_id: str,
    payload: MemoryUpdateRequest,
    db_session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Updates an existing archival memory owned by the authenticated user."""
    from backend.services.memory.memory_store import MemoryVersionConflictError, memory_store
    from backend.services.memory.privacy_guard import validate_memory_content

    try:
        mem_uuid = uuid.UUID(memory_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Memory not found or access denied.")

    validation = validate_memory_content(payload.text, is_explicit=True)
    if not validation.allowed:
        raise HTTPException(
            status_code=422,
            detail=validation.rejection_reason or "Memory content rejected by privacy policy.",
        )

    qclient = _safe_qdrant_client()
    try:
        updated = await memory_store.update_memory(
            session=db_session,
            client=qclient,
            user_id=current_user.id,
            memory_id=mem_uuid,
            new_content=validation.sanitized_content,
            category=payload.category,
            expected_version=payload.expected_version,
        )
    except MemoryVersionConflictError as conflict:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(conflict))

    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Memory not found or access denied.",
        )
    return MemoryItem(**memory_store.serialize_memory(updated))


@router.delete("/memories/{memory_id}", status_code=status.HTTP_200_OK)
async def delete_user_memory(
    memory_id: str,
    db_session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Deletes a specific archival memory owned by the authenticated user."""
    from backend.services.memory.memory_store import memory_store

    try:
        mem_uuid = uuid.UUID(memory_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Memory not found or access denied.")

    qclient = _safe_qdrant_client()
    deleted = await memory_store.soft_delete_memory(
        session=db_session,
        client=qclient,
        user_id=current_user.id,
        memory_id=mem_uuid,
    )
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Memory not found or access denied.",
        )
    return {"status": "deleted", "memory_id": memory_id}


@router.delete("/memories", status_code=status.HTTP_200_OK)
async def delete_user_memories_by_filter(
    category: Optional[str] = Query(default=None, max_length=60),
    delete_all: bool = Query(default=False),
    db_session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Deletes the authenticated user's memories by category or all memories (idempotent)."""
    from backend.services.memory.memory_store import memory_store

    if not category and not delete_all:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Specify either 'category' or 'delete_all=true'.",
        )
    qclient = _safe_qdrant_client()
    deleted_rows = await memory_store.bulk_delete_memories(
        session=db_session,
        client=qclient,
        user_id=current_user.id,
        category=category,
        delete_all=delete_all,
    )
    return {
        "status": "deleted",
        "deleted_count": len(deleted_rows),
        "deleted_ids": [str(m.id) for m in deleted_rows],
    }


@router.post("/memories/bulk-delete", status_code=status.HTTP_200_OK)
async def bulk_delete_user_memories(
    payload: MemoryBulkDeleteRequest,
    db_session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Bulk deletes selected memory IDs, a category, or all memories owned by the authenticated user."""
    from backend.services.memory.memory_store import memory_store

    valid_uuids: List[uuid.UUID] = []
    for raw_id in payload.memory_ids:
        try:
            valid_uuids.append(uuid.UUID(raw_id))
        except ValueError:
            continue

    if not payload.delete_all and not payload.category and not valid_uuids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide memory_ids, category, or delete_all=true.",
        )

    qclient = _safe_qdrant_client()
    deleted_rows = await memory_store.bulk_delete_memories(
        session=db_session,
        client=qclient,
        user_id=current_user.id,
        category=payload.category,
        memory_ids=valid_uuids or None,
        delete_all=payload.delete_all,
    )
    return {
        "status": "deleted",
        "deleted_count": len(deleted_rows),
        "deleted_ids": [str(m.id) for m in deleted_rows],
    }


# ===========================================================================
# Evidence Drawer Context Endpoint (PRD Section 10.3)
# ===========================================================================

class EvidenceChunk(BaseModel):
    text: str
    citation: str
    section: str


class EvidenceContext(BaseModel):
    target: EvidenceChunk
    preceding: Optional[EvidenceChunk] = None
    succeeding: Optional[EvidenceChunk] = None


def _user_can_read_evidence(payload: Dict[str, Any], user: User) -> bool:
    """Fail-closed document RBAC check for evidence reads."""
    from backend.models.enums import CLEARANCE_RANK, ClearanceLevel, Role

    if payload.get("is_live") is not True:
        return False
    if user.role == Role.ADMIN:
        return True
    raw_clearance = payload.get("clearance_level")
    try:
        document_clearance = ClearanceLevel(raw_clearance)
    except (TypeError, ValueError):
        return False
    if document_clearance == ClearanceLevel.PUBLIC:
        return True
    department = payload.get("department")
    if not department or department != user.department:
        return False
    return CLEARANCE_RANK.get(user.clearance_level, 0) >= CLEARANCE_RANK.get(document_clearance, 999)


@router.get("/evidence/{evidence_id}", response_model=EvidenceContext)
async def get_evidence_context(
    evidence_id: str,
    current_user: User = Depends(get_current_user),
):
    """Resolve a signed, user-bound evidence reference into a 3-chunk context sandwich."""
    from qdrant_client import models as qdrant_models
    from backend.core.config import settings as cfg
    from backend.core.evidence_tokens import read_evidence_token
    from backend.db.qdrant import get_qdrant_client

    try:
        point_id = read_evidence_token(evidence_id, current_user.id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Evidence not found")

    client = get_qdrant_client()
    try:
        points = client.retrieve(
            collection_name=cfg.QDRANT_COLLECTION,
            ids=[point_id],
            with_payload=True,
            with_vectors=False,
        )
    except Exception:
        logger.exception("Evidence retrieval failed")
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Evidence is temporarily unavailable")

    if not points:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Evidence not found")
    target_point = points[0]
    payload = target_point.payload or {}
    if not _user_can_read_evidence(payload, current_user):
        # Same response for missing and unauthorized evidence prevents existence probing.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Evidence not found")

    target = EvidenceChunk(
        text=payload.get("text", ""),
        citation=payload.get("citation", ""),
        section=payload.get("section", ""),
    )
    preceding = None
    succeeding = None
    document_id = payload.get("document_id") or payload.get("doc_id")
    chunk_index = payload.get("chunk_index")

    if document_id is not None and isinstance(chunk_index, int):
        for offset, direction in ((-1, "preceding"), (1, "succeeding")):
            try:
                scroll_res = client.scroll(
                    collection_name=cfg.QDRANT_COLLECTION,
                    scroll_filter=qdrant_models.Filter(
                        must=[
                            qdrant_models.FieldCondition(
                                key="document_id",
                                match=qdrant_models.MatchValue(value=str(document_id)),
                            ),
                            qdrant_models.FieldCondition(
                                key="chunk_index",
                                match=qdrant_models.MatchValue(value=chunk_index + offset),
                            ),
                            qdrant_models.FieldCondition(
                                key="is_live",
                                match=qdrant_models.MatchValue(value=True),
                            ),
                        ]
                    ),
                    limit=1,
                    with_payload=True,
                    with_vectors=False,
                )
                neighbours = scroll_res[0] if scroll_res else []
                if not neighbours:
                    continue
                neighbour_payload = neighbours[0].payload or {}
                if not _user_can_read_evidence(neighbour_payload, current_user):
                    continue
                chunk = EvidenceChunk(
                    text=neighbour_payload.get("text", ""),
                    citation=neighbour_payload.get("citation", ""),
                    section=neighbour_payload.get("section", ""),
                )
                if direction == "preceding":
                    preceding = chunk
                else:
                    succeeding = chunk
            except Exception:
                logger.warning("Adjacent evidence lookup failed", exc_info=True)

    return EvidenceContext(target=target, preceding=preceding, succeeding=succeeding)
