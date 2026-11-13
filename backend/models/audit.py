from datetime import datetime, timezone
from typing import ClassVar, Optional
import uuid
from sqlmodel import Field, SQLModel

from backend.models.enums import AuditCategory


class AuditLog(SQLModel, table=True):
    __tablename__: ClassVar[str] = "audit_logs"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: Optional[uuid.UUID] = Field(default=None, index=True, nullable=True)
    event_tag: str = Field(index=True, description="Machine-readable event tag e.g. AUTH_LOGIN, DOC_UPLOAD")
    category: AuditCategory = Field(default=AuditCategory.ADMIN, index=True)
    status_code: int = Field(default=200)
    details: Optional[str] = Field(default=None, description="Non-PII metadata, IDs, or parameters")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), index=True)
