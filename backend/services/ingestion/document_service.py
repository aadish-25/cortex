from datetime import datetime, timezone
import logging
import hashlib
import os
import re
from pathlib import Path
from typing import List, Optional, Tuple
import uuid
from fastapi import HTTPException, status
from qdrant_client import QdrantClient, models
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func
from sqlmodel import and_, col, or_, select

from backend.core.config import settings, BASE_DIR
from backend.models.document import Document
from backend.models.document_log import DocumentProcessingLog
from backend.models.enums import AuditCategory, CLEARANCE_RANK, ClearanceLevel, DocumentStatus, Role
from backend.models.user import User
from backend.services.auth.audit_service import log_audit_event

logger = logging.getLogger(__name__)


def compute_file_hash(content: bytes) -> str:
    """
    Computes SHA-256 hexadecimal digest of raw file bytes (Decision D5).
    Example output: 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'
    """
    return hashlib.sha256(content).hexdigest()


def persist_document_file(document_id: uuid.UUID, filename: str, content: bytes) -> str:
    """Persist original bytes under Cloudinary or local storage (never trust upload paths)."""
    try:
        from backend.services.storage.cloudinary_service import cloudinary_service
        res = cloudinary_service.upload_document(content, document_id, filename)
        return res.get("url") or res.get("storage_path") or ""
    except Exception as exc:
        logger.warning("Cloud storage upload failed (%s); using local disk fallback.", exc)
        extension = Path(filename).suffix.lower()
        if extension not in {".md", ".docx", ".pdf", ".txt"}:
            extension = ".bin"
        storage_dir = BASE_DIR / "storage" / "documents"
        storage_dir.mkdir(parents=True, exist_ok=True)
        destination = storage_dir / f"{document_id}{extension}"
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        temporary.write_bytes(content)
        os.replace(temporary, destination)
        return str(destination)


async def record_document_processing_log(
    session: AsyncSession,
    document_id: uuid.UUID,
    stage: str,
    status: str,
    message: str = "",
) -> None:
    record = DocumentProcessingLog(
        document_id=document_id,
        stage=stage[:64],
        status=status[:32],
        message=message[:1200],
    )
    session.add(record)
    await session.commit()


async def register_document_upload(
    session: AsyncSession,
    filename: str,
    content: bytes,
    department: str,
    clearance_level: ClearanceLevel,
    title: Optional[str] = None,
) -> Tuple[Document, bool]:
    """
    Handles upload validation, SHA-256 deduplication (D5), and staged version initialization (D4).
    Returns (document, is_new_version).
    """
    file_hash = compute_file_hash(content)

    stmt_hash = select(Document).where(Document.file_hash == file_hash, Document.department == department)
    res_hash = await session.execute(stmt_hash)
    if res_hash.scalars().first():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A document with identical content already exists.",
        )

    doc_title = title or filename

    stmt_name = (
        select(Document)
        .where(Document.filename == filename, Document.department == department)
        .order_by(col(Document.version).desc())
    )

    res_name = await session.execute(stmt_name)
    existing_doc = res_name.scalars().first()

    if existing_doc:
        new_version = existing_doc.version + 1
        is_new_version = True

        # Keep the currently-live version active until the replacement is fully indexed.
        session.add(existing_doc)
    else:
        new_version = 1
        is_new_version = False

    new_doc = Document(
        title=doc_title,
        filename=filename,
        file_hash=file_hash,
        department=department,
        clearance_level=clearance_level,
        version=new_version,
        status=DocumentStatus.UPDATING,
        is_live=False,
    )
    session.add(new_doc)
    await session.commit()
    await session.refresh(new_doc)

    await log_audit_event(
        session=session,
        event_tag="DOC_UPLOAD_INITIATED",
        category=AuditCategory.DOCUMENT,
        status_code=200,
        details=f"document_id={new_doc.id}; version={new_version}; department={department}; clearance={clearance_level.value}",
    )
    return new_doc, is_new_version


async def finalize_document_activation(
    session: AsyncSession,
    doc_id: uuid.UUID,
    qdrant_client: Optional[QdrantClient] = None,
) -> Document:
    """
    Decision D4 Atomic Activation in SQL:
    1. Sets new document status=ACTIVE, is_live=True.
    2. Marks older versions of the same filename within the same department with is_live=False.
    """
    stmt = select(Document).where(Document.id == doc_id)
    res = await session.execute(stmt)
    doc = res.scalars().first()
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    # Mark all older versions of this filename as inactive
    older_stmt = (
        select(Document)
        .where(Document.filename == doc.filename, Document.department == doc.department)
        .where(Document.id != doc.id)
    )
    older_res = await session.execute(older_stmt)
    old_versions = older_res.scalars().all()
    if qdrant_client is None:
        from backend.db.qdrant import get_qdrant_client
        qdrant_client = get_qdrant_client()
    for older_doc in old_versions:
        # SQL row and Qdrant payload must agree; otherwise stale versions remain searchable.
        try:
            qdrant_client.set_payload(
                collection_name=settings.QDRANT_COLLECTION,
                payload={"is_live": False},
                points=models.Filter(
                    must=[models.FieldCondition(key="document_id", match=models.MatchValue(value=str(older_doc.id)))]
                ),
            )
        except Exception:
            logger.exception("Failed to deactivate old Qdrant version document_id=%s", older_doc.id)
            raise
        older_doc.is_live = False
        if older_doc.status == DocumentStatus.UPDATING:
            older_doc.status = DocumentStatus.ACTIVE
        session.add(older_doc)

    doc.status = DocumentStatus.ACTIVE
    doc.is_live = True
    doc.updated_at = datetime.now(timezone.utc)
    session.add(doc)
    await session.commit()
    await session.refresh(doc)

    await log_audit_event(
        session=session,
        event_tag="DOC_ACTIVATED",
        category=AuditCategory.DOCUMENT,
        status_code=200,
        details=f"document_id={doc.id}; version={doc.version}; is_live=true",
    )
    return doc


async def mark_document_failed(
    session: AsyncSession,
    doc_id: uuid.UUID,
    error_message: Exception | str,
) -> None:
    """Marks document ingestion as UPDATE_FAILED (Decision D4 recovery)."""
    stmt = select(Document).where(Document.id == doc_id)
    res = await session.execute(stmt)
    doc = res.scalars().first()
    if doc:
        doc.status = DocumentStatus.UPDATE_FAILED
        doc.is_live = False
        doc.updated_at = datetime.now(timezone.utc)
        session.add(doc)
        await session.commit()
        # SQL failure state alone is insufficient: Qdrant applies its own is_live filter.
        try:
            from backend.db.qdrant import get_qdrant_client
            client = get_qdrant_client()
            client.set_payload(
                collection_name=settings.QDRANT_COLLECTION,
                payload={"is_live": False},
                points=models.Filter(
                    must=[models.FieldCondition(key="document_id", match=models.MatchValue(value=str(doc.id)))]
                ),
            )
        except Exception:
            logger.exception("Could not quarantine failed document vectors document_id=%s", doc.id)
        safe_error = str(error_message)[:900]
        safe_error = re.sub(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "[EMAIL]", safe_error)
        safe_error = re.sub(r"\b(?:\+91[-.\s]?)?[6-9]\d{9}\b", "[PHONE]", safe_error)
        safe_error = re.sub(r"[A-Za-z]:\\[^\s]+", "[LOCAL_PATH]", safe_error)
        safe_error = re.sub(r"/(?:home|Users|tmp|var/tmp)/[^\s]+", "[LOCAL_PATH]", safe_error)
        await record_document_processing_log(
            session, doc.id, "processing", "FAILED", f"{type(error_message).__name__}: {safe_error}"
        )
        await log_audit_event(
            session=session,
            event_tag="DOC_UPDATE_FAILED",
            category=AuditCategory.DOCUMENT,
            status_code=500,
            details=f"document_id={doc.id}; version={doc.version}; failure_recorded=true",
        )


async def delete_document(
    session: AsyncSession,
    doc_id: uuid.UUID,
    qdrant_client: QdrantClient,
) -> None:
    """
    Purges document record from PostgreSQL/SQLite and deletes all associated points from Qdrant.
    """
    stmt = select(Document).where(Document.id == doc_id)
    res = await session.execute(stmt)
    doc = res.scalars().first()
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    # 1. Purge points from Qdrant
    qdrant_client.delete(
        collection_name=settings.QDRANT_COLLECTION,
        points_selector=models.FilterSelector(
            filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key="document_id",
                        match=models.MatchValue(value=str(doc.id)),
                    )
                ]
            )
        ),
    )

    # 2. Delete row from SQL database
    doc_id_value = doc.id
    doc_version = doc.version
    storage_path = doc.storage_path
    log_result = await session.execute(
        select(DocumentProcessingLog).where(DocumentProcessingLog.document_id == doc.id)
    )
    for processing_log in log_result.scalars().all():
        await session.delete(processing_log)
    await session.delete(doc)
    await session.commit()
    if storage_path:
        try:
            Path(storage_path).unlink(missing_ok=True)
        except OSError:
            logger.warning("Could not remove stored source for document_id=%s", doc_id_value, exc_info=True)

    # 3. Log audit event
    await log_audit_event(
        session=session,
        event_tag="DOC_DELETED",
        category=AuditCategory.DOCUMENT,
        status_code=200,
        details=f"document_id={doc_id_value}; version={doc_version}; deleted=true",
    )


async def list_documents_for_user_page(
    session: AsyncSession,
    user: User,
    department: Optional[str] = None,
    search: Optional[str] = None,
    status_filter: Optional[DocumentStatus] = None,
    clearance_filter: Optional[ClearanceLevel] = None,
    only_latest: bool = False,
    page: int = 1,
    page_size: int = 25,
) -> Tuple[List[Document], int]:
    """Returns a page of documents with the same RBAC rules used by retrieval."""
    conditions = []

    if user.role != Role.ADMIN:
        allowed_clearances = [
            lvl for lvl, rank in CLEARANCE_RANK.items()
            if rank <= CLEARANCE_RANK[user.clearance_level]
        ]
        rbac_condition = or_(
            Document.clearance_level == ClearanceLevel.PUBLIC,
            and_(
                Document.department == user.department,
                col(Document.clearance_level).in_(allowed_clearances),
            ),
        )
        # Locked/staged versions are intentionally invisible to non-admin users.
        conditions.extend([rbac_condition, Document.is_live.is_(True)])

    if department:
        conditions.append(Document.department == department)
    if search:
        term = f"%{search.strip()}%"
        conditions.append(or_(Document.title.ilike(term), Document.filename.ilike(term)))
    if status_filter:
        conditions.append(Document.status == status_filter)
    if clearance_filter:
        conditions.append(Document.clearance_level == clearance_filter)

    count_stmt = select(func.count(Document.id))
    data_stmt = select(Document)

    if only_latest:
        latest_subq = (
            select(Document.filename, Document.department, func.max(Document.version).label("max_ver"))
            .group_by(Document.filename, Document.department)
            .subquery()
        )
        join_cond = and_(
            Document.filename == latest_subq.c.filename,
            Document.department == latest_subq.c.department,
            Document.version == latest_subq.c.max_ver,
        )
        count_stmt = count_stmt.join(latest_subq, join_cond)
        data_stmt = data_stmt.join(latest_subq, join_cond)

    if conditions:
        count_stmt = count_stmt.where(*conditions)
        data_stmt = data_stmt.where(*conditions)

    total = int((await session.execute(count_stmt)).scalar_one() or 0)
    data_stmt = (
        data_stmt.order_by(col(Document.uploaded_at).desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await session.execute(data_stmt)
    return list(result.scalars().all()), total


async def list_documents_for_user(
    session: AsyncSession,
    user: User,
    department: Optional[str] = None,
) -> List[Document]:
    """Backward-compatible unpaginated helper for internal callers/tests."""
    docs, _ = await list_documents_for_user_page(
        session=session, user=user, department=department, page=1, page_size=10000
    )
    return docs

async def get_document_by_id(
    session: AsyncSession,
    doc_id: uuid.UUID,
    user: User,
) -> Document:
    """Retrieves single document with 3D RBAC clearance check."""
    stmt = select(Document).where(Document.id == doc_id)
    res = await session.execute(stmt)
    doc = res.scalars().first()
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    if user.role != Role.ADMIN:
        if not doc.is_live:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
        if doc.clearance_level != ClearanceLevel.PUBLIC:
            if doc.department != user.department:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied: department mismatch")
            if CLEARANCE_RANK[user.clearance_level] < CLEARANCE_RANK[doc.clearance_level]:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied: insufficient clearance")

    return doc
