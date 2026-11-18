import uuid
from datetime import datetime, timezone
from typing import ClassVar, Optional
from sqlmodel import Field, SQLModel


class EmailChangeRequest(SQLModel, table=True):
    __tablename__: ClassVar[str] = "email_change_requests"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="users.id", index=True)
    user_name: str
    current_email: str
    requested_email: str
    reason: Optional[str] = None
    status: str = Field(default="pending", index=True)  # pending, approved, rejected
    admin_notes: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
