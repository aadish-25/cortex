from datetime import datetime, timezone
from typing import ClassVar, Optional
import uuid
from sqlmodel import Field, SQLModel


class QueryQualityLog(SQLModel, table=True):
    """De-identified quality telemetry for low-confidence and fallback answers."""

    __tablename__: ClassVar[str] = "query_quality_logs"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: Optional[uuid.UUID] = Field(default=None, foreign_key="users.id", index=True)
    conversation_id: Optional[uuid.UUID] = Field(default=None, foreign_key="conversations.id", index=True)
    query_preview: str = Field(default="", max_length=600)
    confidence: str = Field(index=True)
    top_reranker_score: Optional[float] = Field(default=None)
    reranker_scores_json: str = Field(default="[]")
    is_fallback: bool = Field(default=False, index=True)
    reviewed_at: Optional[datetime] = Field(default=None, nullable=True)
    reviewed_by: Optional[uuid.UUID] = Field(default=None, foreign_key="users.id", nullable=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), index=True)
