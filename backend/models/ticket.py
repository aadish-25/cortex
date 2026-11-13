from datetime import datetime, timezone
from typing import ClassVar, Optional
import uuid
from sqlmodel import Field, SQLModel

from backend.models.enums import TicketStatus


class Ticket(SQLModel, table=True):
    __tablename__: ClassVar[str] = "tickets"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="users.id", index=True)
    draft_id: Optional[uuid.UUID] = Field(default=None, foreign_key="action_drafts.id", nullable=True)
    title: str
    description: str
    department: str
    status: TicketStatus = Field(default=TicketStatus.OUTBOX_PENDING)
    jira_key: Optional[str] = Field(default=None, nullable=True, description="Future integration placeholder")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
