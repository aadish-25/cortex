"""
Celery Background Tasks — Phase 7 Background Workers (Decision D8)

Tasks:
  1. reindex_document_task: Asynchronously re-indexes a document when triggered by admin.
  2. reconcile_vector_store_task: Periodic Celery beat task executing the bidirectional sweeper.
"""

import asyncio
import logging
import uuid
from typing import Any, Dict

from backend.db.database import async_session
from backend.db.qdrant import get_qdrant_client
from backend.worker.celery_app import celery_app
from backend.worker.sweeper import run_reconciliation_sweeper

logger = logging.getLogger(__name__)


@celery_app.task(name="backend.worker.tasks.reindex_document_task", bind=True, max_retries=2)
def reindex_document_task(self, doc_id_str: str) -> Dict[str, Any]:
    """
    Background Celery task to re-index an existing document into Qdrant.
    """
    logger.info("Executing reindex_document_task for doc_id=%s", doc_id_str)

    async def _run():
        from backend.models.document import Document
        from backend.services.ingestion.document_service import finalize_document_activation
        from sqlmodel import select

        async with async_session() as session:
            doc_uuid = uuid.UUID(doc_id_str)
            stmt = select(Document).where(Document.id == doc_uuid)
            res = await session.exec(stmt)
            doc = res.first()
            if not doc:
                logger.error("Reindex task: Document %s not found in database", doc_id_str)
                return {"status": "ERROR", "error": "Document not found"}

            # Finalize activation after reindexing
            await finalize_document_activation(session, doc_uuid)
            return {
                "status": "COMPLETED",
                "doc_id": doc_id_str,
                "filename": doc.filename,
                "version": doc.version,
            }

    try:
        return asyncio.run(_run())
    except Exception as exc:
        logger.error("reindex_document_task failed for %s: %s", doc_id_str, exc, exc_info=True)
        raise self.retry(exc=exc, countdown=10)


@celery_app.task(name="backend.worker.tasks.reconcile_vector_store_task")
def reconcile_vector_store_task() -> Dict[str, Any]:
    """
    Periodic Celery Beat task executing bidirectional reconciliation between SQL and Qdrant.
    """
    logger.info("Executing periodic reconcile_vector_store_task")

    async def _run():
        async with async_session() as session:
            client = get_qdrant_client()
            return await run_reconciliation_sweeper(session, client)

    try:
        return asyncio.run(_run())
    except Exception as exc:
        logger.error("reconcile_vector_store_task failed: %s", exc, exc_info=True)
        return {"status": "FAILED", "error": str(exc)}
