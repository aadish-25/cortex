from datetime import datetime, timezone
from typing import ClassVar
import uuid
from sqlmodel import Field, SQLModel


class DocumentProcessingLog(SQLModel, table=True):
    """Append-only operational log for document parsing, indexing and activation."""

    __tablename__: ClassVar[str] = "document_processing_logs"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    document_id: uuid.UUID = Field(index=True)
    stage: str = Field(index=True, max_length=64)
    status: str = Field(index=True, max_length=32)
    message: str = Field(default="", max_length=1200)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), index=True)
