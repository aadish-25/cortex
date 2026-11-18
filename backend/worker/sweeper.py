"""
Bidirectional Reconciliation Sweeper — Phase 7 Background Workers (Decision D8)

Performs bidirectional consistency checks between PostgreSQL and Qdrant:
  1. Forward Pass (SQL -> Qdrant):
     Ensures every active document (is_live=True, status=ACTIVE) has indexed
     vectors in Qdrant. If missing, flags doc for reindex.
  2. Reverse Pass (Qdrant -> SQL):
     Identifies and purges orphan vectors in Qdrant whose parent document
     no longer exists or is no longer marked is_live=True.

Guarded by Redis Distributed Lock ('cortex:lock:reconcile_lock') with a 60s TTL
to prevent concurrent execution across multi-worker deployments.
"""

import logging
import uuid
from typing import Any, Dict, List, Set

from qdrant_client import QdrantClient, models
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from backend.core.config import settings
from backend.db.redis import get_redis_client
from backend.models.document import Document
from backend.models.enums import AuditCategory, DocumentStatus
from backend.services.auth.audit_service import log_audit_event

logger = logging.getLogger(__name__)

RECONCILE_LOCK_KEY = "cortex:lock:reconcile_lock"
RECONCILE_LOCK_TTL = 60  # seconds


async def run_reconciliation_sweeper(
    session: AsyncSession,
    qdrant_client: QdrantClient,
) -> Dict[str, Any]:
    """
    Executes bidirectional reconciliation between PostgreSQL and Qdrant under a Redis lock.
    """
    redis = get_redis_client()
    lock_acquired = False

    # Attempt to acquire non-blocking distributed lock
    if redis is not None:
        try:
            acquired = await redis.set(RECONCILE_LOCK_KEY, "locked", nx=True, ex=RECONCILE_LOCK_TTL)
            if not acquired:
                logger.info("Reconciliation sweeper skipped: lock '%s' is held by another worker.", RECONCILE_LOCK_KEY)
                return {"status": "SKIPPED", "reason": "Distributed lock already held"}
            lock_acquired = True
        except Exception as exc:
            logger.warning("Redis lock acquisition failed (proceeding without lock): %s", exc)

    try:
        stats: Dict[str, Any] = {
            "status": "SUCCESS",
            "active_docs_scanned": 0,
            "missing_docs_flagged": 0,
            "orphan_points_purged": 0,
            "errors": [],
        }

        # -------------------------------------------------------------------
        # 1. Forward Pass: Active SQL Documents -> Qdrant Vector Existence
        # -------------------------------------------------------------------
        stmt = select(Document).where(
            Document.is_live == True,  # noqa: E712
            Document.status == DocumentStatus.ACTIVE,
        )
        res = await session.execute(stmt)
        active_docs = list(res.scalars().all())
        stats["active_docs_scanned"] = len(active_docs)

        active_doc_ids: Set[str] = {str(d.id) for d in active_docs}

        for doc in active_docs:
            try:
                # Probe Qdrant for at least 1 chunk belonging to this doc_id
                filter_cond = models.Filter(
                    must=[
                        models.FieldCondition(
                            key="doc_id",
                            match=models.MatchValue(value=str(doc.id)),
                        )
                    ]
                )
                count_res = qdrant_client.count(
                    collection_name=settings.QDRANT_COLLECTION,
                    count_filter=filter_cond,
                    exact=False,
                )
                if count_res.count == 0:
                    logger.warning("Sweeper: Document '%s' (%s) active in SQL but has 0 vectors in Qdrant! Flagging for reindex.", doc.filename, doc.id)
                    doc.status = DocumentStatus.UPDATING
                    doc.is_live = False
                    session.add(doc)
                    stats["missing_docs_flagged"] += 1
            except Exception as exc:
                stats["errors"].append(f"Forward check failed for doc {doc.id}: {exc}")

        if stats["missing_docs_flagged"] > 0:
            await session.commit()

        # -------------------------------------------------------------------
        # 2. Reverse Pass: Qdrant Vectors -> Active SQL Document Existence
        # -------------------------------------------------------------------
        try:
            # Scroll points in Qdrant to detect orphaned vectors
            # (Limit scroll to batch of 500 to keep memory bounded)
            scroll_res = qdrant_client.scroll(
                collection_name=settings.QDRANT_COLLECTION,
                limit=500,
                with_payload=True,
                with_vectors=False,
            )
            points = scroll_res[0] if scroll_res else []
            orphan_point_ids: List[Any] = []

            for p in points:
                payload = p.payload or {}
                p_doc_id = payload.get("doc_id")
                # If point has doc_id not in active_doc_ids, it is an orphan
                if p_doc_id and str(p_doc_id) not in active_doc_ids:
                    orphan_point_ids.append(p.id)

            if orphan_point_ids:
                logger.info("Sweeper: Purging %d orphan points from Qdrant collection '%s'", len(orphan_point_ids), settings.QDRANT_COLLECTION)
                qdrant_client.delete(
                    collection_name=settings.QDRANT_COLLECTION,
                    points_selector=models.PointIdsList(points=orphan_point_ids),
                )
                stats["orphan_points_purged"] = len(orphan_point_ids)

        except Exception as exc:
            logger.warning("Reverse sweep Qdrant scroll failed: %s", exc)
            stats["errors"].append(f"Reverse sweep failed: {exc}")

        # Audit log the sweeper run
        await log_audit_event(
            session=session,
            event_tag="SWEEPER_RECONCILIATION_COMPLETED",
            category=AuditCategory.ADMIN,
            status_code=200,
            details=f"Sweeper finished: scanned={stats['active_docs_scanned']}, flagged={stats['missing_docs_flagged']}, purged={stats['orphan_points_purged']}",
        )

        return stats

    finally:
        # Release distributed lock
        if lock_acquired and redis is not None:
            try:
                await redis.delete(RECONCILE_LOCK_KEY)
            except Exception as exc:
                logger.warning("Failed to release reconcile lock '%s': %s", RECONCILE_LOCK_KEY, exc)
