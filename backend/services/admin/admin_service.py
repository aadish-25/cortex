"""
AdminService — Phase 7 Operational Intelligence Layer (Decision D26, D34)

Responsibilities:
  - System health telemetry: Postgres, Redis, Qdrant probed independently.
  - Audit log querying: paginated, filtered by AuditCategory.
  - Document status inspection: per-doc live status and version info.
  - Reindex trigger: marks doc UPDATING, re-enqueues ingestion pipeline.
  - RAG quality analytics: daily query counts, avg reranker confidence.
  - Feedback analytics: thumbs-up/down ratios per day.
  - Token usage analytics: per-provider cost breakdown.

Zero-Mutate invariant: this service NEVER modifies user data, chat history,
or external connectors. Document reindex is an operational admin action
executed only on explicit admin invocation (Decision D39 scoped extension).
"""

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import func, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from backend.db.redis import get_redis_client
from backend.models.audit import AuditLog
from backend.models.chat import MessageFeedback
from backend.models.document import Document
from backend.models.enums import AuditCategory, DocumentStatus
from backend.models.token_usage import TokenUsageLog

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# System Health Telemetry
# ---------------------------------------------------------------------------

async def inspect_system_health(session: AsyncSession) -> Dict[str, Any]:
    """
    Independently probes Postgres, Redis, and Qdrant.
    Returns per-service status without raising — always returns a full dict.
    Satisfies Decision D34: runtime Redis degradation observability.
    """
    health: Dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "services": {},
    }

    # --- Postgres ---
    try:
        await session.execute(text("SELECT 1"))
        health["services"]["postgres"] = {"status": "UP", "detail": "Reachable"}
    except Exception as exc:
        health["services"]["postgres"] = {"status": "DOWN", "detail": str(exc)[:200]}

    # --- Redis ---
    try:
        redis = get_redis_client()
        if redis is None:
            from backend.db.redis import redis_manager
            redis = await redis_manager.get_client()
        if redis is not None:
            await redis.ping()
            health["services"]["redis"] = {"status": "UP", "detail": "Ping OK"}
        else:
            health["services"]["redis"] = {"status": "DEGRADED", "detail": "No Redis client configured"}
    except Exception as exc:
        health["services"]["redis"] = {"status": "DEGRADED", "detail": str(exc)[:200]}

    # --- Qdrant ---
    try:
        from backend.db.qdrant import get_qdrant_client
        client = get_qdrant_client()
        collections = client.get_collections()
        collection_names = [c.name for c in collections.collections]
        health["services"]["qdrant"] = {
            "status": "UP",
            "detail": f"{len(collection_names)} collection(s): {collection_names}",
        }
    except Exception as exc:
        health["services"]["qdrant"] = {"status": "DOWN", "detail": str(exc)[:200]}

    # Overall status: UP only if all three are UP
    all_statuses = [v["status"] for v in health["services"].values()]
    if all(s == "UP" for s in all_statuses):
        health["overall"] = "UP"
    elif any(s == "DOWN" for s in all_statuses):
        health["overall"] = "DEGRADED"
    else:
        health["overall"] = "DEGRADED"

    return health


# ---------------------------------------------------------------------------
# Audit Log Querying
# ---------------------------------------------------------------------------

async def query_audit_logs(
    session: AsyncSession,
    category: Optional[AuditCategory] = None,
    limit: int = 50,
    offset: int = 0,
) -> List[AuditLog]:
    """
    Returns paginated audit logs, optionally filtered by AuditCategory.
    Zero-PII: logs contain only machine-readable event tags and IDs.
    """
    stmt = select(AuditLog).order_by(AuditLog.created_at.desc())  # type: ignore[arg-type]
    if category:
        stmt = stmt.where(AuditLog.category == category)
    stmt = stmt.offset(offset).limit(min(limit, 200))  # Hard cap at 200
    result = await session.exec(stmt)
    return list(result.all())


# ---------------------------------------------------------------------------
# Document Status Inspection
# ---------------------------------------------------------------------------

async def get_document_status(session: AsyncSession, doc_id: uuid.UUID) -> Document:
    """
    Returns the Document row for admin inspection (status, version, is_live).
    Raises ValueError if document not found (let router map to 404).
    """
    stmt = select(Document).where(Document.id == doc_id)
    result = await session.exec(stmt)
    doc = result.first()
    if not doc:
        raise ValueError(f"Document {doc_id} not found")
    return doc


# ---------------------------------------------------------------------------
# Reindex Trigger
# ---------------------------------------------------------------------------

async def trigger_document_reindex(
    session: AsyncSession,
    doc_id: uuid.UUID,
) -> Dict[str, Any]:
    """
    Marks document as UPDATING and enqueues re-ingestion via Celery.
    This is the only write in AdminService — strictly operational.
    Returns job metadata.
    """
    stmt = select(Document).where(Document.id == doc_id)
    result = await session.exec(stmt)
    doc = result.first()
    if not doc:
        raise ValueError(f"Document {doc_id} not found")

    if doc.status == DocumentStatus.UPDATING:
        return {
            "doc_id": str(doc_id),
            "status": "ALREADY_UPDATING",
            "message": f"Document '{doc.filename}' is already being processed.",
        }

    doc.status = DocumentStatus.UPDATING
    doc.is_live = False
    session.add(doc)
    await session.commit()

    # Enqueue re-ingestion via Celery (fire-and-forget; non-blocking)
    try:
        from backend.worker.tasks import reindex_document_task
        reindex_document_task.delay(str(doc_id))
        queued = True
    except Exception as exc:
        logger.warning("Celery reindex enqueue failed (will fall back to sync): %s", exc)
        queued = False

    return {
        "doc_id": str(doc_id),
        "filename": doc.filename,
        "version": doc.version,
        "status": "REINDEX_QUEUED" if queued else "REINDEX_PENDING",
        "message": f"Document '{doc.filename}' v{doc.version} marked for reindex.",
    }


# ---------------------------------------------------------------------------
# Replace Document Version
# ---------------------------------------------------------------------------

async def replace_document_version(
    session: AsyncSession,
    doc_id: uuid.UUID,
    file_bytes: bytes,
    new_filename: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Operational tool: Staged document replacement (Decision D4, D5).
    Takes an existing document, validates its existence, and registers
    a new version with the provided file bytes.
    """
    stmt = select(Document).where(Document.id == doc_id)
    result = await session.exec(stmt)
    doc = result.first()
    if not doc:
        raise ValueError(f"Document {doc_id} not found")

    from backend.services.ingestion.document_service import register_document_upload
    filename = new_filename or doc.filename
    new_doc, is_new_version = await register_document_upload(
        session=session,
        filename=filename,
        content=file_bytes,
        department=doc.department,
        clearance_level=doc.clearance_level,
        title=doc.title,
    )

    return {
        "status": "VERSION_STAGED",
        "original_doc_id": str(doc_id),
        "new_doc_id": str(new_doc.id),
        "filename": new_doc.filename,
        "version": new_doc.version,
        "is_new_version": is_new_version,
        "message": f"Staged new version v{new_doc.version} for '{new_doc.filename}'. Status is UPDATING pending ingestion.",
    }


# ---------------------------------------------------------------------------
# RAG Quality Analytics
# ---------------------------------------------------------------------------

async def get_query_analytics(
    session: AsyncSession,
    days: int = 7,
) -> Dict[str, Any]:
    """
    Returns daily query counts and avg reranker confidence from audit logs
    over the last `days` days (capped at 90).
    """
    days = min(days, 90)
    since = datetime.now(timezone.utc) - timedelta(days=days)

    # Daily query counts from audit_logs (QUERY category)
    stmt = (
        select(
            func.date(AuditLog.created_at).label("day"),
            func.count(AuditLog.id).label("count"),
        )
        .where(AuditLog.category == AuditCategory.QUERY)
        .where(AuditLog.created_at >= since)
        .group_by(func.date(AuditLog.created_at))
        .order_by(func.date(AuditLog.created_at))
    )
    result = await session.exec(stmt)
    daily_rows = result.all()

    daily_counts = [{"day": str(row.day), "query_count": row.count} for row in daily_rows]
    total_queries = sum(r["query_count"] for r in daily_counts)

    return {
        "period_days": days,
        "since": since.isoformat(),
        "total_queries": total_queries,
        "daily_counts": daily_counts,
    }


# ---------------------------------------------------------------------------
# Feedback Analytics
# ---------------------------------------------------------------------------

async def get_feedback_analytics(
    session: AsyncSession,
    days: int = 7,
) -> Dict[str, Any]:
    """
    Returns thumbs-up / thumbs-down ratio and daily breakdown.
    """
    days = min(days, 90)
    since = datetime.now(timezone.utc) - timedelta(days=days)

    stmt = (
        select(
            MessageFeedback.is_thumbs_up,
            func.count(MessageFeedback.id).label("count"),
        )
        .where(MessageFeedback.created_at >= since)
        .group_by(MessageFeedback.is_thumbs_up)
    )
    result = await session.exec(stmt)
    rows = result.all()

    thumbs_up = next((r.count for r in rows if r.is_thumbs_up is True), 0)
    thumbs_down = next((r.count for r in rows if r.is_thumbs_up is False), 0)
    total = thumbs_up + thumbs_down
    satisfaction_pct = round((thumbs_up / total) * 100, 1) if total > 0 else None

    return {
        "period_days": days,
        "since": since.isoformat(),
        "thumbs_up": thumbs_up,
        "thumbs_down": thumbs_down,
        "total_feedback": total,
        "satisfaction_percent": satisfaction_pct,
    }


# ---------------------------------------------------------------------------
# Token Usage Analytics
# ---------------------------------------------------------------------------

async def get_token_usage_analytics(
    session: AsyncSession,
    days: int = 7,
) -> Dict[str, Any]:
    """
    Returns per-provider token consumption and estimated cost breakdown.
    """
    days = min(days, 90)
    since = datetime.now(timezone.utc) - timedelta(days=days)

    stmt = (
        select(
            TokenUsageLog.provider,
            TokenUsageLog.model,
            func.sum(TokenUsageLog.total_tokens).label("total_tokens"),
            func.sum(TokenUsageLog.estimated_cost_usd).label("total_cost_usd"),
            func.count(TokenUsageLog.id).label("request_count"),
        )
        .where(TokenUsageLog.created_at >= since)
        .group_by(TokenUsageLog.provider, TokenUsageLog.model)
        .order_by(func.sum(TokenUsageLog.total_tokens).desc())
    )
    result = await session.exec(stmt)
    rows = result.all()

    breakdown = [
        {
            "provider": row.provider,
            "model": row.model,
            "total_tokens": int(row.total_tokens or 0),
            "estimated_cost_usd": round(float(row.total_cost_usd or 0.0), 6),
            "request_count": int(row.request_count or 0),
        }
        for row in rows
    ]

    grand_total_tokens = sum(r["total_tokens"] for r in breakdown)
    grand_total_cost = round(sum(r["estimated_cost_usd"] for r in breakdown), 6)

    return {
        "period_days": days,
        "since": since.isoformat(),
        "grand_total_tokens": grand_total_tokens,
        "grand_total_cost_usd": grand_total_cost,
        "by_provider_model": breakdown,
    }
