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
from backend.models.memory import MemoryStatus, MemorySyncStatus, UserMemory
from backend.services.auth.audit_service import log_audit_event
from backend.services.memory.memory_store import memory_store

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
                    logger.warning("Sweeper: Active document_id=%s has no vectors in Qdrant; flagging for reindex.", doc.id)
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

        # -------------------------------------------------------------------
        # 3. User Memory Reconciliation (PostgreSQL user_memories <-> Qdrant)
        # -------------------------------------------------------------------
        mem_stats = await reconcile_user_memories(session, qdrant_client)
        stats["memories_upserted"] = mem_stats["upserted"]
        stats["memories_deleted"] = mem_stats["deleted"]
        stats["memory_orphans_purged"] = mem_stats["orphans_purged"]
        stats["errors"].extend(mem_stats["errors"])

        # Audit log the sweeper run
        await log_audit_event(
            session=session,
            event_tag="SWEEPER_RECONCILIATION_COMPLETED",
            category=AuditCategory.ADMIN,
            status_code=200,
            details=(
                f"Sweeper finished: scanned={stats['active_docs_scanned']}, "
                f"flagged={stats['missing_docs_flagged']}, purged={stats['orphan_points_purged']}, "
                f"mem_upserted={stats['memories_upserted']}, mem_deleted={stats['memories_deleted']}, "
                f"mem_orphans={stats['memory_orphans_purged']}"
            ),
        )

        return stats

    finally:
        # Release distributed lock
        if lock_acquired and redis is not None:
            try:
                await redis.delete(RECONCILE_LOCK_KEY)
            except Exception as exc:
                logger.warning("Failed to release reconcile lock '%s': %s", RECONCILE_LOCK_KEY, exc)


async def reconcile_user_memories(
    session: AsyncSession,
    qdrant_client: QdrantClient,
) -> Dict[str, Any]:
    """
    Reconciles PostgreSQL `user_memories` with Qdrant `cortex_user_memories`:
      1. Retries rows with `sync_status = pending_upsert`.
      2. Retries rows with `sync_status = pending_delete`.
      3. Purges orphan vectors in Qdrant whose ID is not an active PostgreSQL memory.
    """
    mem_stats: Dict[str, Any] = {
        "upserted": 0,
        "deleted": 0,
        "orphans_purged": 0,
        "errors": [],
    }
    try:
        # 1. Retry pending upserts
        upsert_stmt = select(UserMemory).where(
            UserMemory.sync_status == MemorySyncStatus.PENDING_UPSERT.value,
            UserMemory.status == MemoryStatus.ACTIVE.value,
        )
        pending_upserts = list((await session.execute(upsert_stmt)).scalars().all())
        for mem in pending_upserts:
            if await memory_store._upsert_qdrant_point(qdrant_client, mem):
                mem.sync_status = MemorySyncStatus.SYNCED.value
                session.add(mem)
                mem_stats["upserted"] += 1

        # 2. Retry pending deletes (for deleted or superseded rows)
        delete_stmt = select(UserMemory).where(
            UserMemory.sync_status == MemorySyncStatus.PENDING_DELETE.value,
        )
        pending_deletes = list((await session.execute(delete_stmt)).scalars().all())
        if pending_deletes:
            point_ids = [str(m.id) for m in pending_deletes]
            if await memory_store._delete_qdrant_points(qdrant_client, point_ids):
                for mem in pending_deletes:
                    mem.sync_status = MemorySyncStatus.SYNCED.value
                    session.add(mem)
                mem_stats["deleted"] = len(pending_deletes)

        if mem_stats["upserted"] or mem_stats["deleted"]:
            await session.commit()

        # 3. Reverse pass: purge Qdrant points not active in PostgreSQL
        if qdrant_client.collection_exists(settings.QDRANT_USER_MEMORIES_COLLECTION):
            active_ids = {
                str(mid)
                for mid in (
                    await session.execute(
                        select(UserMemory.id).where(UserMemory.status == MemoryStatus.ACTIVE.value)
                    )
                ).scalars().all()
            }
            scroll_res = qdrant_client.scroll(
                collection_name=settings.QDRANT_USER_MEMORIES_COLLECTION,
                limit=500,
                with_payload=True,
                with_vectors=False,
            )
            points = scroll_res[0] if scroll_res else []
            orphan_ids = [
                str(p.id)
                for p in points
                if (p.payload or {}).get("user_id") and str(p.id) not in active_ids
            ]
            if orphan_ids and await memory_store._delete_qdrant_points(qdrant_client, orphan_ids):
                mem_stats["orphans_purged"] = len(orphan_ids)
    except Exception as exc:
        logger.warning("User memory reconciliation failed: %s", exc)
        mem_stats["errors"].append(f"Memory reconciliation failed: {exc}")

    return mem_stats
