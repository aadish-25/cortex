from datetime import datetime, timezone
from typing import ClassVar, Optional
import uuid
from sqlmodel import Field, SQLModel

from backend.models.enums import FeedbackCategory


class Conversation(SQLModel, table=True):
    __tablename__: ClassVar[str] = "conversations"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="users.id", index=True)
    title: str = Field(default="New Conversation")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Message(SQLModel, table=True):
    __tablename__: ClassVar[str] = "messages"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    conversation_id: uuid.UUID = Field(foreign_key="conversations.id", index=True)
    sender: str = Field(description="'user' or 'assistant'")
    content: str
    citations: Optional[str] = Field(default=None, description="JSON array of inline citations [Doc, page]")
    parent_message_id: Optional[uuid.UUID] = Field(
        default=None,
        foreign_key="messages.id",
        nullable=True,
        description="Linked parent for regenerated variants",
    )
    variant_index: int = Field(default=1, description="Variant version number, e.g. 1, 2")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class MessageFeedback(SQLModel, table=True):
    __tablename__: ClassVar[str] = "message_feedback"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    message_id: uuid.UUID = Field(foreign_key="messages.id", index=True)
    user_id: uuid.UUID = Field(foreign_key="users.id", index=True)
    is_thumbs_up: bool
    category: Optional[FeedbackCategory] = Field(default=None)
    comment: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
