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
from sqlalchemy import func
from sqlmodel import col, select

from backend.core.security import require_roles
from backend.db.database import get_db
from backend.models.enums import AuditCategory, Role
from backend.models.user import User
from backend.models.document import Document
from backend.models.query_quality import QueryQualityLog
from backend.models.email_request import EmailChangeRequest
from backend.services.admin.admin_service import (
    get_document_status,
    count_audit_logs,
    get_quality_analytics,
    list_low_confidence_queries,
    list_document_processing_logs,
    mark_quality_query_reviewed,
    get_feedback_analytics,
    get_query_analytics,
    get_token_usage_analytics,
    inspect_system_health,
    query_audit_logs,
    replace_document_version,
    rollback_document_version,
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
    total = await count_audit_logs(session, category=category)
    return {
        "total": total,
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
        message = str(exc)
        code = status.HTTP_404_NOT_FOUND if "not found" in message.lower() else status.HTTP_400_BAD_REQUEST if "original file" in message.lower() else status.HTTP_503_SERVICE_UNAVAILABLE
        raise HTTPException(status_code=code, detail=message) from exc

    if result.get("status") == "ALREADY_UPDATING":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=result["message"],
        )
    if result.get("status") == "REINDEX_PENDING":
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=result["message"])

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
    content = await file.read(25 * 1024 * 1024 + 1)
    if len(content) > 25 * 1024 * 1024:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Document exceeds the 25 MB upload limit")
    try:
        result = await replace_document_version(
            session=session,
            doc_id=doc_id,
            file_bytes=content,
            new_filename=file.filename,
        )
        if result.get("status") == "REINDEX_PENDING":
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=result["message"])
        return result
    except ValueError as exc:
        code = status.HTTP_404_NOT_FOUND if "not found" in str(exc).lower() else status.HTTP_400_BAD_REQUEST
        raise HTTPException(status_code=code, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/documents/{doc_id}/logs", summary="Paginated document processing history")
async def document_processing_logs(
    doc_id: uuid.UUID,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    session: AsyncSession = Depends(get_db),
    _admin: User = Depends(_require_admin),
) -> Dict[str, Any]:
    document_result = await session.execute(select(Document).where(Document.id == doc_id))
    if document_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Document not found")
    records, total = await list_document_processing_logs(session, doc_id, page, page_size)
    return {
        "items": [
            {"id": str(item.id), "stage": item.stage, "status": item.status, "message": item.message, "created_at": item.created_at.isoformat()}
            for item in records
        ],
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": (total + page_size - 1) // page_size,
    }


@router.post(
    "/documents/{doc_id}/rollback",
    summary="Restore the previous active document version",
    response_model=Dict[str, Any],
)
async def rollback_document(
    doc_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _admin: User = Depends(_require_admin),
) -> Dict[str, Any]:
    try:
        return await rollback_document_version(session, doc_id)
    except ValueError as exc:
        code = status.HTTP_404_NOT_FOUND if "not found" in str(exc).lower() else status.HTTP_409_CONFLICT
        raise HTTPException(status_code=code, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Could not safely restore the previous version") from exc


@router.get("/analytics/quality", summary="RAG quality analytics", response_model=Dict[str, Any])
async def quality_analytics(
    days: int = Query(default=7, ge=1, le=90),
    session: AsyncSession = Depends(get_db),
    _admin: User = Depends(_require_admin),
) -> Dict[str, Any]:
    return await get_quality_analytics(session, days=days)


@router.get("/quality/low-confidence", summary="Paginated low-confidence query review queue")
async def low_confidence_queries(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    reviewed: Optional[bool] = Query(default=False),
    session: AsyncSession = Depends(get_db),
    _admin: User = Depends(_require_admin),
) -> Dict[str, Any]:
    records, total = await list_low_confidence_queries(
        session, page=page, page_size=page_size, reviewed=reviewed
    )
    return {
        "items": [
            {
                "id": str(item.id),
                "query_preview": item.query_preview,
                "confidence": item.confidence,
                "top_reranker_score": item.top_reranker_score,
                "is_fallback": item.is_fallback,
                "created_at": item.created_at.isoformat(),
                "reviewed_at": item.reviewed_at.isoformat() if item.reviewed_at else None,
            }
            for item in records
        ],
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": (total + page_size - 1) // page_size,
    }


@router.patch("/quality/low-confidence/{quality_id}/review", summary="Mark a quality record reviewed")
async def review_quality_query(
    quality_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    admin: User = Depends(_require_admin),
) -> Dict[str, Any]:
    try:
        record = await mark_quality_query_reviewed(session, quality_id, admin.id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"id": str(record.id), "reviewed_at": record.reviewed_at.isoformat() if record.reviewed_at else None}


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


# ---------------------------------------------------------------------------
# Admin Notifications & Email Revision Workflow
# ---------------------------------------------------------------------------

@router.get("/notifications", summary="Live notification center for pending requests")
async def get_admin_notifications(
    session: AsyncSession = Depends(get_db),
    _admin: User = Depends(_require_admin),
) -> Dict[str, Any]:
    """
    Returns pending access requests and pending email revision requests.
    Used by the admin notification bell and review banners.
    """
    # 1. Pending account self-registrations
    pending_users_stmt = (
        select(User)
        .where(User.is_active == False, User.department == "Unassigned")
        .order_by(col(User.created_at).desc())
    )
    res_users = await session.execute(pending_users_stmt)
    pending_users = res_users.scalars().all()

    # 2. Pending email revision requests
    pending_emails_stmt = (
        select(EmailChangeRequest)
        .where(EmailChangeRequest.status == "pending")
        .order_by(col(EmailChangeRequest.created_at).desc())
    )
    res_emails = await session.execute(pending_emails_stmt)
    pending_emails = res_emails.scalars().all()

    items = []
    for u in pending_users:
        items.append({
            "id": str(u.id),
            "type": "access_request",
            "title": f"New access request: {u.name}",
            "subtitle": f"Personal email: {u.email}",
            "created_at": u.created_at.isoformat() if u.created_at else "",
            "user_id": str(u.id),
            "name": u.name,
            "email": u.email,
        })

    for e in pending_emails:
        items.append({
            "id": str(e.id),
            "type": "email_request",
            "title": f"Email revision requested by {e.user_name}",
            "subtitle": f"Change from {e.current_email} to {e.requested_email}" + (f" ({e.reason})" if e.reason else ""),
            "created_at": e.created_at.isoformat() if e.created_at else "",
            "request_id": str(e.id),
            "user_id": str(e.user_id),
            "current_email": e.current_email,
            "requested_email": e.requested_email,
            "reason": e.reason,
        })

    items.sort(key=lambda x: x["created_at"], reverse=True)

    return {
        "total_pending": len(pending_users) + len(pending_emails),
        "pending_registrations_count": len(pending_users),
        "pending_email_requests_count": len(pending_emails),
        "items": items,
    }


@router.get("/email-requests", summary="List email revision requests")
async def list_email_requests(
    status_filter: Optional[str] = Query(default=None, alias="status"),
    session: AsyncSession = Depends(get_db),
    _admin: User = Depends(_require_admin),
):
    """List corporate email revision requests submitted by employees."""
    stmt = select(EmailChangeRequest)
    if status_filter:
        stmt = stmt.where(EmailChangeRequest.status == status_filter)
    stmt = stmt.order_by(col(EmailChangeRequest.created_at).desc())
    res = await session.execute(stmt)
    return res.scalars().all()


@router.post("/email-requests/{request_id}/approve", summary="Approve an email revision request")
async def approve_email_request(
    request_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    admin: User = Depends(_require_admin),
):
    """Approve a pending corporate email change request and update the user's primary email."""
    stmt = select(EmailChangeRequest).where(EmailChangeRequest.id == request_id)
    res = await session.execute(stmt)
    req = res.scalars().first()
    if not req:
        raise HTTPException(status_code=404, detail="Email change request not found")

    if req.status != "pending":
        raise HTTPException(status_code=400, detail=f"Request is already {req.status}")

    user_stmt = select(User).where(User.id == req.user_id)
    u_res = await session.execute(user_stmt)
    target_user = u_res.scalars().first()
    if not target_user:
        raise HTTPException(status_code=404, detail="Target user account not found")

    # Collision double-check
    dup_stmt = select(User).where(User.email == req.requested_email, User.id != target_user.id)
    if (await session.execute(dup_stmt)).scalars().first():
        raise HTTPException(status_code=409, detail="Requested email is already in use by another user")

    from datetime import datetime, timezone
    target_user.email = req.requested_email
    target_user.token_version = max(1, target_user.token_version) + 1
    target_user.updated_at = datetime.now(timezone.utc)

    req.status = "approved"
    req.updated_at = datetime.now(timezone.utc)

    session.add(target_user)
    session.add(req)
    await session.commit()
    await session.refresh(req)

    from backend.services.auth.audit_service import log_audit_event
    await log_audit_event(
        session=session,
        event_tag="USER_EMAIL_REVISED_APPROVED",
        category=AuditCategory.ADMIN,
        user_id=admin.id,
        status_code=200,
        details=f"target_user_id={target_user.id}; old_email={req.current_email}; new_email={req.requested_email}",
    )

    return {"message": f"Approved email change. Corporate email updated to {req.requested_email}", "status": "approved"}


@router.post("/email-requests/{request_id}/reject", summary="Reject an email revision request")
async def reject_email_request(
    request_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    admin: User = Depends(_require_admin),
):
    """Reject a pending corporate email change request."""
    stmt = select(EmailChangeRequest).where(EmailChangeRequest.id == request_id)
    res = await session.execute(stmt)
    req = res.scalars().first()
    if not req:
        raise HTTPException(status_code=404, detail="Email change request not found")

    if req.status != "pending":
        raise HTTPException(status_code=400, detail=f"Request is already {req.status}")

    from datetime import datetime, timezone
    req.status = "rejected"
    req.updated_at = datetime.now(timezone.utc)

    session.add(req)
    await session.commit()

    from backend.services.auth.audit_service import log_audit_event
    await log_audit_event(
        session=session,
        event_tag="USER_EMAIL_REVISED_REJECTED",
        category=AuditCategory.ADMIN,
        user_id=admin.id,
        status_code=200,
        details=f"target_user_id={req.user_id}; requested_email={req.requested_email}",
    )

    return {"message": "Email change request rejected", "status": "rejected"}
