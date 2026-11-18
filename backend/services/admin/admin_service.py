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
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import case, func, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from backend.core.config import settings
from backend.db.redis import get_redis_client
from backend.models.audit import AuditLog
from backend.models.chat import MessageFeedback
from backend.models.document import Document
from backend.models.enums import AuditCategory, DocumentStatus
from backend.models.token_usage import TokenUsageLog
from backend.models.query_quality import QueryQualityLog
from backend.models.document_log import DocumentProcessingLog
from backend.services.auth.audit_service import log_audit_event

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
        health["services"]["postgres"] = {"status": "DOWN", "detail": "Database connection failed"}

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
        health["services"]["redis"] = {"status": "DEGRADED", "detail": "Redis connection failed"}

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
        health["services"]["qdrant"] = {"status": "DOWN", "detail": "Vector database connection failed"}

    # Celery worker inspection is bounded so an offline broker cannot stall the admin page.
    try:
        from backend.worker.celery_app import celery_app
        inspector = celery_app.control.inspect(timeout=1.0)
        worker_pings = await asyncio.wait_for(asyncio.to_thread(inspector.ping), timeout=2.0)
        worker_count = len(worker_pings or {})
        health["services"]["celery"] = {
            "status": "UP" if worker_count else "DEGRADED",
            "detail": f"{worker_count} worker(s) responding" if worker_count else "No Celery workers responded",
        }
    except Exception:
        health["services"]["celery"] = {"status": "DEGRADED", "detail": "Worker inspection unavailable"}

    # Provider key availability is reported separately from live infrastructure probes.
    health["llm_providers"] = {
        "groq": "CONFIGURED" if (settings.GROQ_API_KEY or settings.GROQ_API_KEYS) else "NOT_CONFIGURED",
        "openrouter": "CONFIGURED" if (settings.OPENROUTER_API_KEY or settings.OPENROUTER_API_KEYS) else "NOT_CONFIGURED",
        "gemini": "CONFIGURED" if (settings.GEMINI_API_KEY or settings.GEMINI_API_KEYS) else "NOT_CONFIGURED",
        "meta": "CONFIGURED" if settings.META_API_KEY else "NOT_CONFIGURED",
    }

    # Overall status: UP only if all infrastructure services are UP.
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
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def count_audit_logs(
    session: AsyncSession,
    category: Optional[AuditCategory] = None,
) -> int:
    stmt = select(func.count(AuditLog.id))
    if category:
        stmt = stmt.where(AuditLog.category == category)
    result = await session.execute(stmt)
    return int(result.scalar_one() or 0)


# ---------------------------------------------------------------------------
# Document Status Inspection
# ---------------------------------------------------------------------------

async def get_document_status(session: AsyncSession, doc_id: uuid.UUID) -> Document:
    """
    Returns the Document row for admin inspection (status, version, is_live).
    Raises ValueError if document not found (let router map to 404).
    """
    stmt = select(Document).where(Document.id == doc_id)
    result = await session.execute(stmt)
    doc = result.scalars().first()
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
    result = await session.execute(stmt)
    doc = result.scalars().first()
    if not doc:
        raise ValueError(f"Document {doc_id} not found")

    latest_result = await session.execute(
        select(Document).where(Document.filename == doc.filename, Document.department == doc.department).order_by(Document.version.desc()).limit(1)
    )
    latest_doc = latest_result.scalar_one_or_none()
    if latest_doc is None or latest_doc.id != doc.id:
        raise ValueError("Only the latest document version can be reindexed")

    if doc.status == DocumentStatus.UPDATING:
        return {
            "doc_id": str(doc_id),
            "status": "ALREADY_UPDATING",
            "message": "This document is already being processed.",
        }
    from pathlib import Path
    if not doc.storage_path or not Path(doc.storage_path).is_file():
        raise ValueError("Original file is unavailable; this document must be uploaded again before it can be reindexed.")

    doc.status = DocumentStatus.UPDATING
    doc.is_live = False
    session.add(doc)
    await session.commit()
    from backend.services.ingestion.document_service import record_document_processing_log
    await record_document_processing_log(session, doc.id, "reindex", "QUEUED", "Administrator requested document reindexing.")

    # Quarantine existing vectors before rebuilding; never leave stale content searchable.
    try:
        from backend.db.qdrant import get_qdrant_client
        from qdrant_client import models
        get_qdrant_client().set_payload(
            collection_name=settings.QDRANT_COLLECTION,
            payload={"is_live": False},
            points=models.Filter(
                must=[models.FieldCondition(key="document_id", match=models.MatchValue(value=str(doc_id)))]
            ),
        )
    except Exception as exc:
        logger.exception("Could not quarantine vectors before reindex")
        doc.status = DocumentStatus.UPDATE_FAILED
        session.add(doc)
        await session.commit()
        raise ValueError("Vector store could not quarantine the existing version; reindex was not queued") from exc

    # Enqueue re-ingestion via Celery.
    try:
        from backend.worker.tasks import reindex_document_task
        reindex_document_task.delay(str(doc_id))
        queued = True
    except Exception as exc:
        logger.warning("Celery reindex enqueue failed: %s", exc)
        queued = False
        doc.status = DocumentStatus.UPDATE_FAILED
        session.add(doc)
        await session.commit()
        from backend.services.ingestion.document_service import record_document_processing_log
        await record_document_processing_log(session, doc.id, "reindex_queue", "FAILED", "Worker queue unavailable; retry can be requested from Admin → Documents.")

    await log_audit_event(
        session=session,
        event_tag="DOC_REINDEX_REQUESTED",
        category=AuditCategory.DOCUMENT,
        status_code=202 if queued else 503,
        details=f"document_id={doc_id}; version={doc.version}; queued={str(queued).lower()}",
    )
    return {
        "doc_id": str(doc_id),
        "version": doc.version,
        "status": "REINDEX_QUEUED" if queued else "REINDEX_PENDING",
        "message": "Document queued for reindex." if queued else "Document marked for reindex but the worker queue is unavailable.",
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
    result = await session.execute(stmt)
    doc = result.scalars().first()
    if not doc:
        raise ValueError(f"Document {doc_id} not found")
    latest_result = await session.execute(
        select(Document).where(Document.filename == doc.filename, Document.department == doc.department).order_by(Document.version.desc()).limit(1)
    )
    latest_doc = latest_result.scalar_one_or_none()
    if latest_doc is None or latest_doc.id != doc.id:
        raise ValueError("Replacement must be uploaded against the latest document version")
    if not file_bytes:
        raise ValueError("Replacement file is empty")
    if len(file_bytes) > 25 * 1024 * 1024:
        raise ValueError("Replacement file exceeds the 25 MB upload limit")

    from pathlib import Path
    uploaded_name = new_filename or doc.filename
    original_extension = Path(doc.filename).suffix.lower()
    uploaded_extension = Path(uploaded_name).suffix.lower()
    if original_extension not in {".md", ".docx", ".pdf", ".txt"} or uploaded_extension not in {".md", ".docx", ".pdf", ".txt"}:
        raise ValueError("Unsupported replacement file type")
    if uploaded_extension != original_extension:
        raise ValueError("Replacement must use the same file extension as the current document")
    # Keep the existing filename as the stable document-family key, regardless of the local upload name.
    filename = doc.filename

    from backend.services.ingestion.document_service import (
        mark_document_failed,
        persist_document_file,
        record_document_processing_log,
        register_document_upload,
    )
    new_doc, is_new_version = await register_document_upload(
        session=session,
        filename=filename,
        content=file_bytes,
        department=doc.department,
        clearance_level=doc.clearance_level,
        title=doc.title,
    )
    try:
        new_doc.storage_path = persist_document_file(new_doc.id, filename, file_bytes)
        session.add(new_doc)
        await session.commit()
        await record_document_processing_log(session, new_doc.id, "replacement", "STAGED", "Replacement file uploaded and queued for indexing.")
    except Exception as exc:
        await mark_document_failed(session, new_doc.id, exc)
        raise

    try:
        from backend.worker.tasks import reindex_document_task
        task = reindex_document_task.delay(str(new_doc.id))
        queue_status = "REINDEX_QUEUED"
        job_id = getattr(task, "id", None)
    except Exception as exc:
        logger.warning("Document replacement queue unavailable: %s", exc)
        new_doc.status = DocumentStatus.UPDATE_FAILED
        session.add(new_doc)
        await session.commit()
        await record_document_processing_log(session, new_doc.id, "replacement_queue", "FAILED", "Worker queue unavailable; retry can be requested from Admin → Documents.")
        queue_status = "REINDEX_PENDING"
        job_id = None

    await log_audit_event(
        session=session,
        event_tag="DOC_REPLACEMENT_STAGED",
        category=AuditCategory.DOCUMENT,
        status_code=202 if queue_status == "REINDEX_QUEUED" else 503,
        details=f"original_document_id={doc_id}; new_document_id={new_doc.id}; version={new_doc.version}; queued={str(queue_status == 'REINDEX_QUEUED').lower()}",
    )
    return {
        "status": queue_status,
        "original_doc_id": str(doc_id),
        "new_doc_id": str(new_doc.id),
        "version": new_doc.version,
        "is_new_version": is_new_version,
        "job_id": job_id,
        "message": "Replacement version staged. It will become active only after successful indexing.",
    }


async def list_document_processing_logs(
    session: AsyncSession,
    doc_id: uuid.UUID,
    page: int = 1,
    page_size: int = 25,
) -> tuple[List[DocumentProcessingLog], int]:
    total = int((await session.execute(
        select(func.count(DocumentProcessingLog.id)).where(DocumentProcessingLog.document_id == doc_id)
    )).scalar_one() or 0)
    result = await session.execute(
        select(DocumentProcessingLog)
        .where(DocumentProcessingLog.document_id == doc_id)
        .order_by(DocumentProcessingLog.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return list(result.scalars().all()), total


async def rollback_document_version(session: AsyncSession, doc_id: uuid.UUID) -> Dict[str, Any]:
    """Restore the latest prior version for the document family, even if a newer replacement failed."""
    requested_result = await session.execute(select(Document).where(Document.id == doc_id))
    requested = requested_result.scalar_one_or_none()
    if requested is None:
        raise ValueError("Document not found")

    versions_result = await session.execute(
        select(Document)
        .where(Document.filename == requested.filename, Document.department == requested.department)
        .order_by(Document.version.desc())
    )
    versions = list(versions_result.scalars().all())
    if len(versions) < 2:
        raise ValueError("No previous document version is available to restore")

    latest = versions[0]
    previous = next((item for item in versions[1:] if item.status == DocumentStatus.ACTIVE), None)
    if previous is None:
        raise ValueError("No previously indexed document version is available to restore")
    from backend.db.qdrant import get_qdrant_client
    from qdrant_client import models
    client = get_qdrant_client()

    # Fail closed: make all versions inactive, then activate exactly one prior version.
    for version in versions:
        client.set_payload(
            collection_name=settings.QDRANT_COLLECTION,
            payload={"is_live": False},
            points=models.Filter(must=[models.FieldCondition(key="document_id", match=models.MatchValue(value=str(version.id))) ]),
        )
    client.set_payload(
        collection_name=settings.QDRANT_COLLECTION,
        payload={"is_live": True},
        points=models.Filter(must=[models.FieldCondition(key="document_id", match=models.MatchValue(value=str(previous.id))) ]),
    )

    for version in versions:
        version.is_live = version.id == previous.id
        if version.id == previous.id:
            version.status = DocumentStatus.ACTIVE
        elif version.id == latest.id and version.status == DocumentStatus.UPDATING:
            version.status = DocumentStatus.UPDATE_FAILED
        session.add(version)
    await session.commit()

    from backend.services.ingestion.document_service import record_document_processing_log
    await record_document_processing_log(session, latest.id, "rollback", "COMPLETED", f"Restored version {previous.version}.")
    await log_audit_event(
        session=session,
        event_tag="DOC_VERSION_ROLLED_BACK",
        category=AuditCategory.DOCUMENT,
        status_code=200,
        details=f"inactive_document_id={latest.id}; restored_document_id={previous.id}; restored_version={previous.version}",
    )
    return {
        "status": "ROLLED_BACK",
        "restored_document_id": str(previous.id),
        "restored_version": previous.version,
        "inactive_document_id": str(latest.id),
    }


# ---------------------------------------------------------------------------
# RAG Quality Analytics
# ---------------------------------------------------------------------------

async def get_query_analytics(
    session: AsyncSession,
    days: int = 7,
) -> Dict[str, Any]:
    """Return daily query counts from the dedicated quality telemetry ledger."""
    days = min(max(days, 1), 90)
    since = datetime.now(timezone.utc) - timedelta(days=days)
    stmt = (
        select(
            func.date(QueryQualityLog.created_at).label("day"),
            func.count(QueryQualityLog.id).label("count"),
        )
        .where(QueryQualityLog.created_at >= since)
        .group_by(func.date(QueryQualityLog.created_at))
        .order_by(func.date(QueryQualityLog.created_at))
    )
    result = await session.execute(stmt)
    daily_rows = result.all()
    daily_counts = [{"day": str(row.day), "query_count": row.count} for row in daily_rows]
    return {
        "period_days": days,
        "since": since.isoformat(),
        "total_queries": sum(item["query_count"] for item in daily_counts),
        "daily_counts": daily_counts,
    }


async def get_quality_analytics(session: AsyncSession, days: int = 7) -> Dict[str, Any]:
    days = min(max(days, 1), 90)
    since = datetime.now(timezone.utc) - timedelta(days=days)
    result = await session.execute(
        select(
            func.count(QueryQualityLog.id).label("total"),
            func.sum(case((QueryQualityLog.is_fallback.is_(True), 1), else_=0)).label("fallbacks"),
            func.avg(QueryQualityLog.top_reranker_score).label("average_score"),
        ).where(QueryQualityLog.created_at >= since)
    )
    row = result.one()
    total = int(row.total or 0)
    fallbacks = int(row.fallbacks or 0)
    low_result = await session.execute(
        select(func.count(QueryQualityLog.id)).where(
            QueryQualityLog.created_at >= since,
            QueryQualityLog.confidence.in_(["LOW", "FUZZY"]),
        )
    )
    low_confidence = int(low_result.scalar_one() or 0)
    return {
        "period_days": days,
        "total_queries": total,
        "fallback_queries": fallbacks,
        "fallback_rate_percent": round((fallbacks / total) * 100, 1) if total else 0.0,
        "low_confidence_queries": low_confidence,
        "average_reranker_score": round(float(row.average_score), 4) if row.average_score is not None else None,
    }


async def list_low_confidence_queries(
    session: AsyncSession,
    page: int = 1,
    page_size: int = 25,
    reviewed: Optional[bool] = None,
) -> tuple[List[QueryQualityLog], int]:
    conditions = [QueryQualityLog.confidence.in_(["LOW", "FUZZY"])]
    if reviewed is True:
        conditions.append(QueryQualityLog.reviewed_at.is_not(None))
    elif reviewed is False:
        conditions.append(QueryQualityLog.reviewed_at.is_(None))
    total = int((await session.execute(select(func.count(QueryQualityLog.id)).where(*conditions))).scalar_one() or 0)
    result = await session.execute(
        select(QueryQualityLog)
        .where(*conditions)
        .order_by(QueryQualityLog.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return list(result.scalars().all()), total


async def mark_quality_query_reviewed(
    session: AsyncSession,
    quality_id: uuid.UUID,
    admin_id: uuid.UUID,
) -> QueryQualityLog:
    result = await session.execute(select(QueryQualityLog).where(QueryQualityLog.id == quality_id))
    record = result.scalar_one_or_none()
    if record is None:
        raise ValueError("Quality record not found")
    record.reviewed_at = datetime.now(timezone.utc)
    record.reviewed_by = admin_id
    session.add(record)
    await session.commit()
    await session.refresh(record)
    return record


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
    result = await session.execute(stmt)
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
    result = await session.execute(stmt)
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
