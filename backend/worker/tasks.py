"""Celery tasks for document indexing and vector-store reconciliation.

Heavy parsing, vector, and sweeper modules are imported lazily inside task
execution so the Celery master process stays lean before prefork().
"""
import asyncio
import logging
from pathlib import Path
from typing import Any, Dict
import uuid

from backend.worker.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="backend.worker.tasks.reindex_document_task",
    bind=True,
    max_retries=2,
    acks_late=True,
    reject_on_worker_lost=True,
)
def reindex_document_task(self, doc_id_str: str) -> Dict[str, Any]:
    """Rebuild a document's vectors from its retained original source file."""
    from qdrant_client import models
    from sqlmodel import select

    from backend.core.config import settings
    from backend.db.database import async_session
    from backend.db.qdrant import get_qdrant_client
    from backend.models.document import Document
    from backend.services.ingestion.chunking_service import chunk_sections
    from backend.services.ingestion.document_service import (
        finalize_document_activation,
        mark_document_failed,
        record_document_processing_log,
    )
    from backend.services.ingestion.ingestion_service import parse_file
    from backend.services.vector.vector_service import activate_document_version, index_document_chunks

    logger.info("Starting document reindex task for document_id=%s", doc_id_str)

    async def _run() -> Dict[str, Any]:
        async with async_session() as session:
            doc_uuid = uuid.UUID(doc_id_str)
            result = await session.execute(select(Document).where(Document.id == doc_uuid))
            doc = result.scalar_one_or_none()
            if doc is None:
                return {"status": "ERROR", "error": "Document not found"}
            if not doc.storage_path or not Path(doc.storage_path).is_file():
                await mark_document_failed(session, doc.id, "Original source file is unavailable")
                return {"status": "FAILED", "error": "Original source file is unavailable"}

            try:
                await record_document_processing_log(session, doc.id, "source_read", "STARTED", "Reading retained original file.")
                source = Path(doc.storage_path).read_bytes()
                sections = parse_file(doc.filename, source)
                await record_document_processing_log(session, doc.id, "parse", "COMPLETED", f"Parsed {len(sections)} source sections.")
                chunks = chunk_sections(
                    sections=sections,
                    title=doc.title,
                    version=doc.version,
                    department=doc.department,
                )
                if not chunks:
                    raise ValueError("Document parser produced no indexable content")
                await record_document_processing_log(session, doc.id, "chunk", "COMPLETED", f"Prepared {len(chunks)} chunks for indexing.")

                client = get_qdrant_client()
                client.delete(
                    collection_name=settings.QDRANT_COLLECTION,
                    points_selector=models.FilterSelector(
                        filter=models.Filter(
                            must=[
                                models.FieldCondition(key="document_id", match=models.MatchValue(value=str(doc.id))),
                                models.FieldCondition(key="version", match=models.MatchValue(value=doc.version)),
                            ]
                        )
                    ),
                )
                indexed = index_document_chunks(
                    client=client,
                    document_id=doc.id,
                    version=doc.version,
                    department=doc.department,
                    clearance_level=doc.clearance_level,
                    chunks=chunks,
                    is_live=False,
                )
                if indexed <= 0:
                    raise ValueError("No document chunks were indexed")
                await record_document_processing_log(session, doc.id, "vector_index", "COMPLETED", f"Indexed {indexed} chunks into Qdrant.")
                activate_document_version(client, doc.id, doc.version)
                await finalize_document_activation(session, doc.id, qdrant_client=client)
                await record_document_processing_log(session, doc.id, "activation", "COMPLETED", "Document version is active and prior vectors are inactive.")
                return {
                    "status": "COMPLETED",
                    "doc_id": doc_id_str,
                    "version": doc.version,
                    "chunks_indexed": indexed,
                }
            except Exception as exc:
                logger.error("Document reindex attempt failed for document_id=%s (%s)", doc_id_str, type(exc).__name__, exc_info=True)
                await record_document_processing_log(
                    session, doc.id, "processing", "RETRYING", f"Attempt failed ({type(exc).__name__}); retry scheduled if attempts remain."
                )
                raise

    try:
        return asyncio.run(_run())
    except Exception as exc:
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc, countdown=10)

        async def _mark_final_failure() -> None:
            async with async_session() as failure_session:
                await mark_document_failed(failure_session, uuid.UUID(doc_id_str), exc)

        asyncio.run(_mark_final_failure())
        logger.error("Document reindex exhausted retries for document_id=%s", doc_id_str)
        return {"status": "FAILED", "error": "Reindex failed after retries"}


@celery_app.task(
    name="backend.worker.tasks.reconcile_vector_store_task",
    acks_late=True,
    reject_on_worker_lost=True,
)
def reconcile_vector_store_task() -> Dict[str, Any]:
    """Run periodic bidirectional reconciliation between SQL and Qdrant."""
    from backend.db.database import async_session
    from backend.db.qdrant import get_qdrant_client
    from backend.worker.sweeper import run_reconciliation_sweeper

    async def _run():
        async with async_session() as session:
            return await run_reconciliation_sweeper(session, get_qdrant_client())

    try:
        return asyncio.run(_run())
    except Exception as exc:
        logger.error("Vector reconciliation failed: %s", exc, exc_info=True)
        return {"status": "FAILED", "error": str(exc)}


@celery_app.task(name="backend.worker.tasks.heartbeat_task")
def heartbeat_task() -> str:
    """Lightweight periodic worker heartbeat updating Redis for sub-millisecond health probes."""
    import time
    import redis
    from backend.core.config import settings
    try:
        r = redis.Redis.from_url(settings.REDIS_URL)
        r.set("cortex:celery:worker_heartbeat", str(time.time()), ex=60)
        return "OK"
    except Exception:
        return "ERROR"

