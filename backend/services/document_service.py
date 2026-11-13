import hashlib
from typing import Optional, Tuple
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select, col

from backend.models.document import Document
from backend.models.enums import AuditCategory, ClearanceLevel, DocumentStatus
from backend.services.audit_service import log_audit_event


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
