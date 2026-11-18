"""
Admin Router — Phase 7 Administrative API (Decision D26, D34)

All endpoints require Role.ADMIN. Zero-write invariant applies to all
read-only endpoints; document reindex is the sole operational write.

Endpoints:
  GET  /api/v1/admin/health                     — System health telemetry
  GET  /api/v1/admin/audit-logs                 — Paginated audit log query
  GET  /api/v1/admin/documents/{doc_id}/status  — Single document status
  POST /api/v1/admin/documents/{doc_id}/reindex — Trigger document reindex
  GET  /api/v1/admin/analytics/queries          — Daily query count analytics
  GET  /api/v1/admin/analytics/feedback         — Thumbs-up/down analytics
  GET  /api/v1/admin/analytics/tokens           — Token usage & cost breakdown
"""

import uuid
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.security import require_roles
from backend.db.database import get_db
from backend.models.enums import AuditCategory, Role
from backend.models.user import User
from backend.services.admin.admin_service import (
    get_document_status,
    get_feedback_analytics,
    get_query_analytics,
    get_token_usage_analytics,
    inspect_system_health,
    query_audit_logs,
    replace_document_version,
    trigger_document_reindex,
)

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])

# Shared dependency — all admin routes require Admin role
_require_admin = require_roles(Role.ADMIN)


# ---------------------------------------------------------------------------
# GET /api/v1/admin/health
# ---------------------------------------------------------------------------

@router.get("/health", summary="System health telemetry", response_model=Dict[str, Any])
async def admin_health(
    session: AsyncSession = Depends(get_db),
    _admin: User = Depends(_require_admin),
) -> Dict[str, Any]:
    """
    Returns live status of Postgres, Redis, and Qdrant.
    Overall field is UP only when all three are reachable.
    Never raises — always returns a full telemetry dict (Decision D34).
    """
    return await inspect_system_health(session)


# ---------------------------------------------------------------------------
# GET /api/v1/admin/audit-logs
# ---------------------------------------------------------------------------

@router.get("/audit-logs", summary="Query audit logs", response_model=Dict[str, Any])
async def get_audit_logs(
    category: Optional[AuditCategory] = Query(default=None, description="Filter by audit category"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_db),
    _admin: User = Depends(_require_admin),
) -> Dict[str, Any]:
    """
    Returns paginated, zero-PII audit log records.
    Optionally filter by AuditCategory (AUTH, DOCUMENT, QUERY, WORKFLOW, ADMIN).
    """
    logs = await query_audit_logs(session, category=category, limit=limit, offset=offset)
    return {
        "total_returned": len(logs),
        "offset": offset,
        "limit": limit,
        "category_filter": category.value if category else None,
        "logs": [
            {
                "id": str(log.id),
                "event_tag": log.event_tag,
                "category": log.category.value,
                "status_code": log.status_code,
                "details": log.details,
                "user_id": str(log.user_id) if log.user_id else None,
                "created_at": log.created_at.isoformat(),
            }
            for log in logs
        ],
    }


# ---------------------------------------------------------------------------
# GET /api/v1/admin/documents/{doc_id}/status
# ---------------------------------------------------------------------------

@router.get(
    "/documents/{doc_id}/status",
    summary="Inspect document status",
    response_model=Dict[str, Any],
)
async def document_status(
    doc_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _admin: User = Depends(_require_admin),
) -> Dict[str, Any]:
    """
    Returns the live status, version, and metadata for a single document.
    Useful for diagnosing stalled UPDATING documents.
    """
    try:
        doc = await get_document_status(session, doc_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    return {
        "id": str(doc.id),
        "filename": doc.filename,
        "version": doc.version,
        "status": doc.status.value,
        "is_live": doc.is_live,
        "department": doc.department,
        "clearance_level": doc.clearance_level.value,
        "uploaded_at": doc.uploaded_at.isoformat() if doc.uploaded_at else None,
    }


# ---------------------------------------------------------------------------
# POST /api/v1/admin/documents/{doc_id}/reindex
# ---------------------------------------------------------------------------

@router.post(
    "/documents/{doc_id}/reindex",
    summary="Trigger document reindex",
    response_model=Dict[str, Any],
    status_code=status.HTTP_202_ACCEPTED,
)
async def reindex_document(
    doc_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _admin: User = Depends(_require_admin),
) -> Dict[str, Any]:
    """
    Marks a document as UPDATING and re-enqueues it for vector re-ingestion.
    Returns 202 Accepted with job metadata.
    Returns 404 if document not found.
    Returns 409 if document is already being reindexed.
    """
    try:
        result = await trigger_document_reindex(session, doc_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    if result.get("status") == "ALREADY_UPDATING":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=result["message"],
        )

    return result


# ---------------------------------------------------------------------------
# POST /api/v1/admin/documents/{doc_id}/replace
# ---------------------------------------------------------------------------

@router.post(
    "/documents/{doc_id}/replace",
    summary="Replace document version",
    response_model=Dict[str, Any],
    status_code=status.HTTP_202_ACCEPTED,
)
async def replace_document(
    doc_id: uuid.UUID,
    file: UploadFile = File(..., description="Replacement document content"),
    session: AsyncSession = Depends(get_db),
    _admin: User = Depends(_require_admin),
) -> Dict[str, Any]:
    """
    Operational tool: Staged document replacement.
    Registers a new version of the specified document with the provided file bytes.
    The new version is staged with status=UPDATING and is_live=False until activated.
    """
    content = await file.read()
    try:
        return await replace_document_version(
            session=session,
            doc_id=doc_id,
            file_bytes=content,
            new_filename=file.filename,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# GET /api/v1/admin/analytics/queries
# ---------------------------------------------------------------------------

@router.get(
    "/analytics/queries",
    summary="Daily query count analytics",
    response_model=Dict[str, Any],
)
async def query_analytics(
    days: int = Query(default=7, ge=1, le=90, description="Lookback window in days"),
    session: AsyncSession = Depends(get_db),
    _admin: User = Depends(_require_admin),
) -> Dict[str, Any]:
    """
    Returns total and daily query counts from audit logs over the last N days.
    """
    return await get_query_analytics(session, days=days)


# ---------------------------------------------------------------------------
# GET /api/v1/admin/analytics/feedback
# ---------------------------------------------------------------------------

@router.get(
    "/analytics/feedback",
    summary="Thumbs-up / thumbs-down feedback analytics",
    response_model=Dict[str, Any],
)
async def feedback_analytics(
    days: int = Query(default=7, ge=1, le=90, description="Thumbs-up/down lookback in days"),
    session: AsyncSession = Depends(get_db),
    _admin: User = Depends(_require_admin),
) -> Dict[str, Any]:
    """
    Returns thumbs-up/down counts and satisfaction percentage over the last N days.
    """
    return await get_feedback_analytics(session, days=days)


# ---------------------------------------------------------------------------
# GET /api/v1/admin/analytics/tokens
# ---------------------------------------------------------------------------

@router.get(
    "/analytics/tokens",
    summary="Token usage and cost analytics",
    response_model=Dict[str, Any],
)
async def token_analytics(
    days: int = Query(default=7, ge=1, le=90, description="Token usage lookback in days"),
    session: AsyncSession = Depends(get_db),
    _admin: User = Depends(_require_admin),
) -> Dict[str, Any]:
    """
    Returns per-provider/model token consumption and estimated cost breakdown.
    Includes grand totals over the lookback period.
    """
    return await get_token_usage_analytics(session, days=days)
