from datetime import datetime, timezone
from enum import Enum
from typing import Any, ClassVar, Dict, List, Optional
import uuid
from pydantic import BaseModel
from sqlmodel import Field, SQLModel
from sqlalchemy import UniqueConstraint

from backend.models.enums import FeedbackCategory


# ==========================================
# Database Table Models (SQLModel)
# ==========================================

class Conversation(SQLModel, table=True):
    __tablename__: ClassVar[str] = "conversations"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="users.id", index=True)
    title: str = Field(default="New Conversation")
    summary: Optional[str] = Field(default=None, nullable=True, description="Cached executive context summary of older compacted turns")
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
    is_complete: bool = Field(default=True, description="False when generation was interrupted and the saved answer is partial")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class MessageFeedback(SQLModel, table=True):
    __tablename__: ClassVar[str] = "message_feedback"
    __table_args__ = (UniqueConstraint("message_id", "user_id", name="uq_message_feedback_user"),)

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    message_id: uuid.UUID = Field(foreign_key="messages.id", index=True)
    user_id: uuid.UUID = Field(foreign_key="users.id", index=True)
    is_thumbs_up: bool
    category: Optional[FeedbackCategory] = Field(default=None)
    comment: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ==========================================
# Conversational Planning & DAG Models (Pydantic)
# ==========================================

class ChatRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class ChatMessage(BaseModel):
    role: ChatRole
    content: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class PlanTaskType(str, Enum):
    INTERNAL_RAG = "INTERNAL_RAG"          # Qdrant 3D RBAC knowledge search
    EXTERNAL_WEB = "EXTERNAL_WEB"          # Sanitized external search
    STRUCTURED_LOOKUP = "STRUCTURED_LOOKUP"# Parameterized SQL queries (Phase 4)
    ACTION_WORKFLOW = "ACTION_WORKFLOW"    # HITL Jira ticketing (Phase 4)
    GITHUB_SEARCH = "GITHUB_SEARCH"        # Read-only code, PRs, issues (Phase 6)
    DRIVE_SEARCH = "DRIVE_SEARCH"          # Read-only Shared Drive & Docs (Phase 6)
    ADMIN_OPERATION = "ADMIN_OPERATION"    # Privileged operational inspection/reindex tools
    DIRECT_CHAT = "DIRECT_CHAT"            # Chit-chat, greetings, clarifications


class PlanTask(BaseModel):
    id: int
    type: PlanTaskType
    query: str
    depends_on: List[int] = Field(default_factory=list)
    status: str = "PENDING"
    output: Optional[Any] = None
    citations: List[str] = Field(default_factory=list)


class ExecutionPlan(BaseModel):
    tasks: List[PlanTask]
    original_query: str
    rewritten_query: Optional[str] = None
    was_rewritten: bool = False
