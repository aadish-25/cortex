from typing import List, Optional
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from qdrant_client import QdrantClient
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.security import get_current_user
from backend.db.database import get_db
from backend.db.qdrant import get_qdrant_client
from backend.models.memory import MemorySourceType
from backend.models.user import User
from backend.services.memory.memory_store import MemoryVersionConflictError, memory_store
from backend.services.memory.privacy_guard import validate_memory_content

router = APIRouter(prefix="/api/v1/chat/memories", tags=["Memories"])


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


@router.get("")
async def list_user_memories(
    limit: int = Query(default=25, ge=1, le=100),
    cursor: Optional[str] = Query(default=None),
    category: Optional[str] = Query(default=None, max_length=60),
    search: Optional[str] = Query(default=None, max_length=200),
    status_filter: str = Query(default="active", alias="status", pattern="^(active|superseded|all)$"),
    db_session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Paginated, filterable list of the authenticated user's archival memories."""
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

    serialized = [MemoryItem(**memory_store.serialize_memory(m)).model_dump() for m in items]
    if search and search.strip() and not serialized and status_filter == "active":
        sem_hits = await memory_store.semantic_search(
            session=db_session,
            client=_safe_qdrant_client(),
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


@router.get("/search")
async def search_user_memories(
    q: str = Query(..., min_length=1, max_length=300),
    category: Optional[str] = Query(default=None, max_length=60),
    limit: int = Query(default=10, ge=1, le=50),
    db_session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Hybrid semantic + keyword search strictly scoped to the authenticated user."""
    sem_hits = await memory_store.semantic_search(
        session=db_session,
        client=_safe_qdrant_client(),
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


@router.post("", response_model=MemoryItem, status_code=status.HTTP_201_CREATED)
async def create_user_memory(
    payload: MemoryCreateRequest,
    db_session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Manually adds a validated personal memory owned by the authenticated user."""
    validation = validate_memory_content(payload.text, is_explicit=True)
    if not validation.allowed:
        raise HTTPException(
            status_code=422,
            detail=validation.rejection_reason or "Memory content rejected by privacy policy.",
        )

    created = await memory_store.create_memory(
        session=db_session,
        client=_safe_qdrant_client(),
        user_id=current_user.id,
        content=validation.sanitized_content,
        category=payload.category,
        source_type=MemorySourceType.MANUAL_UI.value,
        confidence=1.0,
    )
    return MemoryItem(**memory_store.serialize_memory(created))


@router.get("/{memory_id}", response_model=MemoryItem)
async def get_user_memory_detail(
    memory_id: str,
    db_session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Retrieves a single memory owned by the authenticated user."""
    try:
        mem_uuid = uuid.UUID(memory_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Memory not found or access denied.")

    mem = await memory_store.get_memory(session=db_session, user_id=current_user.id, memory_id=mem_uuid)
    if mem is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Memory not found or access denied.")
    return MemoryItem(**memory_store.serialize_memory(mem))


@router.patch("/{memory_id}", response_model=MemoryItem)
async def update_user_memory(
    memory_id: str,
    payload: MemoryUpdateRequest,
    db_session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Updates an existing archival memory owned by the authenticated user."""
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

    try:
        updated = await memory_store.update_memory(
            session=db_session,
            client=_safe_qdrant_client(),
            user_id=current_user.id,
            memory_id=mem_uuid,
            new_content=validation.sanitized_content,
            category=payload.category,
            expected_version=payload.expected_version,
        )
    except MemoryVersionConflictError as conflict:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(conflict))

    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Memory not found or access denied.")
    return MemoryItem(**memory_store.serialize_memory(updated))


@router.delete("/{memory_id}", status_code=status.HTTP_200_OK)
async def delete_user_memory(
    memory_id: str,
    db_session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Deletes a specific archival memory owned by the authenticated user."""
    try:
        mem_uuid = uuid.UUID(memory_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Memory not found or access denied.")

    deleted = await memory_store.soft_delete_memory(
        session=db_session,
        client=_safe_qdrant_client(),
        user_id=current_user.id,
        memory_id=mem_uuid,
    )
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Memory not found or access denied.")
    return {"status": "deleted", "memory_id": memory_id}


@router.delete("", status_code=status.HTTP_200_OK)
async def delete_user_memories_by_filter(
    category: Optional[str] = Query(default=None, max_length=60),
    delete_all: bool = Query(default=False),
    db_session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Deletes the authenticated user's memories by category or all memories (idempotent)."""
    if not category and not delete_all:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Specify either 'category' or 'delete_all=true'.",
        )
    deleted_rows = await memory_store.bulk_delete_memories(
        session=db_session,
        client=_safe_qdrant_client(),
        user_id=current_user.id,
        category=category,
        delete_all=delete_all,
    )
    return {
        "status": "deleted",
        "deleted_count": len(deleted_rows),
        "deleted_ids": [str(m.id) for m in deleted_rows],
    }


@router.post("/bulk-delete", status_code=status.HTTP_200_OK)
async def bulk_delete_user_memories(
    payload: MemoryBulkDeleteRequest,
    db_session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Bulk deletes selected memory IDs, a category, or all memories owned by the authenticated user."""
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

    deleted_rows = await memory_store.bulk_delete_memories(
        session=db_session,
        client=_safe_qdrant_client(),
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
