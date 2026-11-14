import uuid
import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from backend.models.audit import AuditLog
from backend.models.document import Document
from backend.models.enums import ClearanceLevel, DocumentStatus
from backend.services.document_service import compute_file_hash, register_document_upload


def test_compute_file_hash():
    content = b"Cortex Enterprise AI Policy"
    h1 = compute_file_hash(content)
    h2 = compute_file_hash(content)
    assert h1 == h2
    assert len(h1) == 64  # SHA-256 produces 64 hex characters


@pytest.mark.asyncio
async def test_register_new_document(db_session: AsyncSession):
    uid = uuid.uuid4().hex[:8]
    content = f"Welcome to Engineering Policy {uid}".encode()
    filename = f"test_eng_policy_{uid}.md"

    doc, is_new_version = await register_document_upload(
        session=db_session,
        filename=filename,
        content=content,
        department="Engineering",
        clearance_level=ClearanceLevel.INTERNAL,
        title="Engineering Policy",
    )
    assert doc.id is not None
    assert doc.version == 1
    assert is_new_version is False
    assert doc.is_live is False  # Staged version flip: False until Qdrant confirms indexing
    assert doc.status == DocumentStatus.UPDATING
    assert doc.file_hash == compute_file_hash(content)


@pytest.mark.asyncio
async def test_register_duplicate_hash_rejected(db_session: AsyncSession):
    uid = uuid.uuid4().hex[:8]
    content = f"Unique Content for Deduplication Test {uid}".encode()

    # First upload
    await register_document_upload(
        session=db_session,
        filename=f"first_doc_{uid}.txt",
        content=content,
        department="HR",
        clearance_level=ClearanceLevel.INTERNAL,
    )

    # Second upload with identical bytes (even if filename differs) -> must 409 Conflict (D5)
    with pytest.raises(HTTPException) as exc_info:
        await register_document_upload(
            session=db_session,
            filename=f"second_doc_copy_{uid}.txt",
            content=content,
            department="HR",
            clearance_level=ClearanceLevel.INTERNAL,
        )
    assert exc_info.value.status_code == 409
    assert "identical content already exists" in exc_info.value.detail


@pytest.mark.asyncio
async def test_register_new_version_same_filename(db_session: AsyncSession):
    uid = uuid.uuid4().hex[:8]
    filename = f"versioned_policy_{uid}.md"
    v1_content = f"Version 1 Content {uid}".encode()
    v2_content = f"Version 2 Updated Content {uid}".encode()

    # Upload Version 1
    doc_v1, is_v1_new = await register_document_upload(
        session=db_session,
        filename=filename,
        content=v1_content,
        department="Finance",
        clearance_level=ClearanceLevel.CONFIDENTIAL,
    )
    assert doc_v1.version == 1
    assert is_v1_new is False

    # Upload Version 2 with same filename but new content (D4)
    doc_v2, is_v2_new = await register_document_upload(
        session=db_session,
        filename=filename,
        content=v2_content,
        department="Finance",
        clearance_level=ClearanceLevel.CONFIDENTIAL,
    )
    assert doc_v2.version == 2
    assert is_v2_new is True
    assert doc_v2.is_live is False
    assert doc_v2.status == DocumentStatus.UPDATING


@pytest.mark.asyncio
async def test_document_audit_log_created(db_session: AsyncSession):
    uid = uuid.uuid4().hex[:8]
    content = f"Audit logging test document bytes {uid}".encode()
    filename = f"audit_test_doc_{uid}.md"

    doc, _ = await register_document_upload(
        session=db_session,
        filename=filename,
        content=content,
        department="Engineering",
        clearance_level=ClearanceLevel.INTERNAL,
    )

    stmt = select(AuditLog).where(AuditLog.event_tag == "DOC_UPLOAD_INITIATED")
    res = await db_session.execute(stmt)
    audit_entry = res.scalars().first()
    assert audit_entry is not None
    assert audit_entry.status_code == 200
