from datetime import datetime, timezone
import hashlib
from typing import List, Optional, Tuple
import uuid
from fastapi import HTTPException, status
from qdrant_client import QdrantClient, models
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import and_, col, or_, select

from backend.core.config import settings
from backend.models.document import Document
from backend.models.enums import AuditCategory, CLEARANCE_RANK, ClearanceLevel, DocumentStatus, Role
from backend.models.user import User
from backend.services.auth.audit_service import log_audit_event


def compute_file_hash(content: bytes) -> str:
    """
    Computes SHA-256 hexadecimal digest of raw file bytes (Decision D5).
    Example output: 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'
    """
    return hashlib.sha256(content).hexdigest()


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

    stmt_hash = select(Document).where(Document.file_hash == file_hash)
    res_hash = await session.execute(stmt_hash)
    if res_hash.scalars().first():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A document with identical content already exists.",
        )

    doc_title = title or filename

    stmt_name = (
        select(Document)
        .where(Document.filename == filename)
        .order_by(col(Document.version).desc())
    )

    res_name = await session.execute(stmt_name)
    existing_doc = res_name.scalars().first()

    if existing_doc:
        new_version = existing_doc.version + 1
        is_new_version = True

        existing_doc.status = DocumentStatus.UPDATING
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
        details=f"Uploaded '{filename}' (v{new_version}, dept={department}, clearance={clearance_level.value})",
    )
    return new_doc, is_new_version


async def finalize_document_activation(
    session: AsyncSession,
    doc_id: uuid.UUID,
) -> Document:
    """
    Decision D4 Atomic Activation in SQL:
    1. Sets new document status=ACTIVE, is_live=True.
    2. Marks older versions of the same filename with is_live=False.
    """
    stmt = select(Document).where(Document.id == doc_id)
    res = await session.execute(stmt)
    doc = res.scalars().first()
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    # Mark all older versions of this filename as inactive
    older_stmt = (
        select(Document)
        .where(Document.filename == doc.filename)
        .where(Document.id != doc.id)
    )
    older_res = await session.execute(older_stmt)
    for older_doc in older_res.scalars().all():
        older_doc.is_live = False
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
        details=f"Document '{doc.filename}' v{doc.version} is now active (is_live=True)",
    )
    return doc


async def mark_document_failed(
    session: AsyncSession,
    doc_id: uuid.UUID,
    error_message: str,
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
        await log_audit_event(
            session=session,
            event_tag="DOC_UPDATE_FAILED",
            category=AuditCategory.DOCUMENT,
            status_code=500,
            details=f"Ingestion failed for '{doc.filename}' v{doc.version}: {error_message[:200]}",
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
    doc_title = doc.title
    doc_filename = doc.filename
    doc_version = doc.version
    await session.delete(doc)
    await session.commit()

    # 3. Log audit event
    await log_audit_event(
        session=session,
        event_tag="DOC_DELETED",
        category=AuditCategory.DOCUMENT,
        status_code=200,
        details=f"Purged document '{doc_filename}' v{doc_version} (id={doc_id})",
    )


async def list_documents_for_user(
    session: AsyncSession,
    user: User,
    department: Optional[str] = None,
) -> List[Document]:
    """
    Returns documents filtered by 3D RBAC:
    - Admin: All documents.
    - Employee/Manager: Only documents matching user department and clearance rank, plus Public documents.
    """
    stmt = select(Document)

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
        stmt = stmt.where(rbac_condition)

    if department:
        stmt = stmt.where(Document.department == department)

    stmt = stmt.order_by(col(Document.uploaded_at).desc())
    res = await session.execute(stmt)
    return list(res.scalars().all())


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
        if doc.clearance_level != ClearanceLevel.PUBLIC:
            if doc.department != user.department:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied: department mismatch")
            if CLEARANCE_RANK[user.clearance_level] < CLEARANCE_RANK[doc.clearance_level]:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied: insufficient clearance")

    return doc
