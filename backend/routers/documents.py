from typing import List, Optional
import uuid
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.security import get_current_user, require_document_manager
from backend.db.database import get_db
from backend.db.qdrant import get_qdrant_client
from backend.models.document import DocumentRead
from backend.models.enums import ClearanceLevel, Role
from backend.models.user import User
from backend.services.ingestion.chunking_service import chunk_sections
from backend.services.ingestion.document_service import (
    delete_document,
    finalize_document_activation,
    get_document_by_id,
    list_documents_for_user,
    mark_document_failed,
    register_document_upload,
)
from backend.services.ingestion.ingestion_service import parse_file
from backend.services.vector.vector_service import (
    activate_document_version,
    index_document_chunks,
)

router = APIRouter(prefix="/api/v1/documents", tags=["documents"])

SUPPORTED_EXTENSIONS = {".md", ".docx", ".pdf", ".txt"}


class DocumentUploadResponse(BaseModel):
    document: DocumentRead
    chunks_indexed: int
    is_new_version: bool


class DeleteDocumentResponse(BaseModel):
    status: str
    id: uuid.UUID


@router.post("/upload", response_model=DocumentUploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_document(
    file: UploadFile = File(...),
    department: str = Form(...),
    clearance_level: ClearanceLevel = Form(default=ClearanceLevel.INTERNAL),
    title: Optional[str] = Form(default=None),
    current_user: User = Depends(require_document_manager),
    session: AsyncSession = Depends(get_db),
):
    """
    Multipart document ingestion pipeline (Phase 2.4):
    1. Validates file format and department permissions (Manager scoped to own dept, Admin unscoped).
    2. SHA-256 byte deduplication (Decision D5): Rejects exact duplicate bytes with 409.
    3. Staged DB registration: Creates row with is_live=False, status=UPDATING (Decision D4).
    4. Multi-format parsing (MD, DOCX, PDF, TXT) and heading extraction.
    5. Sliding window chunking (500 chars, 100 overlap) + D6 Identity Header stamping.
    6. FastEmbed vector generation (Dense 384d + Sparse BM25) and staged Qdrant indexing (is_live=False).
    7. Atomic switchover: Flips is_live=True and purges older version chunks.
    """
    # Manager RBAC guard: Managers can only upload documents for their own department
    if current_user.role == Role.MANAGER and current_user.department != department:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Managers can only upload documents for their own department ('{current_user.department}')",
        )

    # Validate file extension
    filename = file.filename or "uploaded_document"
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file format '{ext}'. Supported formats: {sorted(SUPPORTED_EXTENSIONS)}",
        )

    content = await file.read()
    if not content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is empty",
        )

    doc_title = title or filename.rsplit(".", 1)[0].replace("_", " ")

    # 1. Register in SQL database (handles SHA-256 deduplication and staged status)
    new_doc, is_new_version = await register_document_upload(
        session=session,
        filename=filename,
        content=content,
        department=department,
        clearance_level=clearance_level,
        title=doc_title,
    )

    try:
        # 2. Parse file sections
        sections = parse_file(filename, content)

        # 3. Chunk sections with D6 Identity Header stamping
        chunks = chunk_sections(
            sections=sections,
            title=new_doc.title,
            version=new_doc.version,
            department=new_doc.department,
        )

        # 4. FastEmbed + Staged Qdrant indexing (is_live=False)
        qdrant_client = get_qdrant_client()
        points_indexed = index_document_chunks(
            client=qdrant_client,
            document_id=new_doc.id,
            version=new_doc.version,
            department=new_doc.department,
            clearance_level=new_doc.clearance_level,
            chunks=chunks,
            is_live=False,
        )

        # 5. Atomic switchover: Flip new version to is_live=True and purge older version chunks
        activate_document_version(
            client=qdrant_client,
            document_id=new_doc.id,
            active_version=new_doc.version,
        )

        # 6. Finalize in SQL database: Mark active and commit
        activated_doc = await finalize_document_activation(session, new_doc.id)

        return DocumentUploadResponse(
            document=DocumentRead.model_validate(activated_doc),
            chunks_indexed=points_indexed,
            is_new_version=is_new_version,
        )

    except Exception as exc:
        # Decision D4: Record failure and prevent partial search pollution
        await mark_document_failed(session, new_doc.id, str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Document ingestion failed: {exc}",
        )


@router.get("/", response_model=List[DocumentRead])
async def list_documents(
    department: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    """
    List all documents visible to the authenticated user under 3D RBAC rules.
    """
    docs = await list_documents_for_user(session=session, user=current_user, department=department)
    return [DocumentRead.model_validate(d) for d in docs]


@router.get("/{doc_id}", response_model=DocumentRead)
async def get_document(
    doc_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    """
    Retrieve single document details with 3D RBAC clearance check.
    """
    doc = await get_document_by_id(session=session, doc_id=doc_id, user=current_user)
    return DocumentRead.model_validate(doc)


@router.delete("/{doc_id}", response_model=DeleteDocumentResponse)
async def remove_document(
    doc_id: uuid.UUID,
    current_user: User = Depends(require_document_manager),
    session: AsyncSession = Depends(get_db),
):
    """
    Permanently deletes a document from SQL and purges its vector points from Qdrant.
    Managers can only delete documents belonging to their department; Admins can delete any.
    """
    # Check permissions
    doc = await get_document_by_id(session=session, doc_id=doc_id, user=current_user)
    if current_user.role == Role.MANAGER and doc.department != current_user.department:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Managers can only delete documents belonging to their own department",
        )

    qdrant_client = get_qdrant_client()
    await delete_document(session=session, doc_id=doc_id, qdrant_client=qdrant_client)
    return DeleteDocumentResponse(status="deleted", id=doc_id)
