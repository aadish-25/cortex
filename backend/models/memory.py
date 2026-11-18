from datetime import datetime, timezone
from enum import Enum
from typing import ClassVar, Optional
import uuid

from sqlmodel import Field, SQLModel


class MemoryCategory(str, Enum):
    PREFERENCE = "preference"
    TECH_STACK = "tech_stack"
    PROJECT_CONTEXT = "project_context"
    GOAL = "goal"
    WORKFLOW = "workflow"
    LOCATION = "location"
    GENERAL = "general"


VALID_MEMORY_CATEGORIES = {c.value for c in MemoryCategory}


def normalize_memory_category(raw: Optional[str]) -> str:
    """Normalizes any raw/LLM-supplied category into a canonical MemoryCategory value."""
    if not raw or not isinstance(raw, str):
        return MemoryCategory.GENERAL.value
    cleaned = raw.strip().lower().replace(" ", "_").replace("-", "_")
    aliases = {
        "preferences": MemoryCategory.PREFERENCE.value,
        "coding_preference": MemoryCategory.PREFERENCE.value,
        "coding_preferences": MemoryCategory.PREFERENCE.value,
        "response_preference": MemoryCategory.PREFERENCE.value,
        "style": MemoryCategory.PREFERENCE.value,
        "tech": MemoryCategory.TECH_STACK.value,
        "technology": MemoryCategory.TECH_STACK.value,
        "technology_preference": MemoryCategory.TECH_STACK.value,
        "stack": MemoryCategory.TECH_STACK.value,
        "project": MemoryCategory.PROJECT_CONTEXT.value,
        "projects": MemoryCategory.PROJECT_CONTEXT.value,
        "career": MemoryCategory.GOAL.value,
        "learning": MemoryCategory.GOAL.value,
        "learning_goal": MemoryCategory.GOAL.value,
        "goals": MemoryCategory.GOAL.value,
        "career_objective": MemoryCategory.GOAL.value,
        "workflows": MemoryCategory.WORKFLOW.value,
        "process": MemoryCategory.WORKFLOW.value,
        "office": MemoryCategory.LOCATION.value,
        "personal": MemoryCategory.GENERAL.value,
        "personal_life": MemoryCategory.GENERAL.value,
    }
    if cleaned in VALID_MEMORY_CATEGORIES:
        return cleaned
    return aliases.get(cleaned, MemoryCategory.GENERAL.value)


class MemoryStatus(str, Enum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    DELETED = "deleted"


class MemorySyncStatus(str, Enum):
    SYNCED = "synced"
    PENDING_UPSERT = "pending_upsert"
    PENDING_DELETE = "pending_delete"


class MemorySourceType(str, Enum):
    EXPLICIT_CHAT = "explicit_chat"
    AUTO_EXTRACTED = "auto_extracted"
    MANUAL_UI = "manual_ui"
    BACKFILLED = "backfilled"


class UserMemory(SQLModel, table=True):
    """
    Canonical long-term archival memory record stored in PostgreSQL (source of truth).
    Qdrant collection `cortex_user_memories` stores the dense embedding using the same UUID.
    """

    __tablename__: ClassVar[str] = "user_memories"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="users.id", index=True)
    content: str = Field(description="Canonical durable memory fact or preference")
    category: str = Field(default=MemoryCategory.GENERAL.value, index=True)
    status: str = Field(default=MemoryStatus.ACTIVE.value, index=True)
    confidence: float = Field(default=1.0)
    source_type: str = Field(default=MemorySourceType.AUTO_EXTRACTED.value)
    source_conversation_id: Optional[uuid.UUID] = Field(
        default=None,
        foreign_key="conversations.id",
        nullable=True,
        index=True,
    )
    source_message_id: Optional[uuid.UUID] = Field(
        default=None,
        foreign_key="messages.id",
        nullable=True,
    )
    supersedes_memory_id: Optional[uuid.UUID] = Field(
        default=None,
        foreign_key="user_memories.id",
        nullable=True,
        index=True,
    )
    version: int = Field(default=1, description="Monotonic version for optimistic concurrency control")
    sync_status: str = Field(default=MemorySyncStatus.PENDING_UPSERT.value, index=True)
    expires_at: Optional[datetime] = Field(default=None, nullable=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    deleted_at: Optional[datetime] = Field(default=None, nullable=True)
