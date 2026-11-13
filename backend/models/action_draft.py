from datetime import datetime, timezone
from typing import ClassVar, Optional
import uuid
from sqlmodel import Field, SQLModel


class ActionDraft(SQLModel, table=True):
    __tablename__: ClassVar[str] = "action_drafts"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="users.id", index=True)
    action_type: str = Field(description="Action name, e.g. 'RAISE_TICKET'")
    payload: str = Field(description="JSON serialized payload of action details")
    expires_at: datetime = Field(description="Expiration timestamp, strictly 15 minutes from creation")
    is_committed: bool = Field(default=False, description="Idempotent flag: set to True when executed")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
