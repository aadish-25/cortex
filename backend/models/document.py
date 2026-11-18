from datetime import datetime, timezone
from typing import ClassVar, Optional
import uuid
from sqlmodel import Field, SQLModel

from backend.models.enums import ClearanceLevel, DocumentStatus


class DocumentBase(SQLModel):
    title: str
    filename: str
    department: str = Field(index=True)
    clearance_level: ClearanceLevel = Field(default=ClearanceLevel.INTERNAL)


class Document(DocumentBase, table=True):
    __tablename__: ClassVar[str] = "documents"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    file_hash: str = Field(index=True, description="SHA-256 hash of file content for deduplication")
    storage_path: Optional[str] = Field(default=None, nullable=True, description="Server-side path to the original file for safe reindexing")
    version: int = Field(default=1)
    status: DocumentStatus = Field(default=DocumentStatus.ACTIVE, index=True)
    is_live: bool = Field(default=True, index=True, description="False during staged version flip ingestion")
    uploaded_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class DocumentRead(DocumentBase):
    id: uuid.UUID
    file_hash: str
    version: int
    status: DocumentStatus
    is_live: bool
    is_latest: bool = False
    has_source_file: bool = False
    uploaded_at: datetime
    updated_at: datetime
