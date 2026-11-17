from datetime import datetime, timezone
from typing import ClassVar, Optional
import uuid
from sqlmodel import Field, SQLModel


class TokenUsageLog(SQLModel, table=True):
    """
    Tracks token consumption, provider attribution, and equivalent commercial cost
    for admin dashboard observability and per-user cost allocation.
    Includes cost_badge and pricing_note to transparently distinguish exact published
    pricing from assumed benchmark valuations.
    """
    __tablename__: ClassVar[str] = "token_usage_logs"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: Optional[uuid.UUID] = Field(default=None, foreign_key="users.id", index=True, nullable=True)
    provider: str = Field(index=True, description="'groq', 'openrouter', 'gemini', 'meta'")
    model: str = Field(index=True, description="Full model identifier used")
    prompt_tokens: int = Field(default=0)
    completion_tokens: int = Field(default=0)
    total_tokens: int = Field(default=0)
    estimated_cost_usd: float = Field(default=0.0, description="Equivalent commercial dollar value")
    is_free_tier: bool = Field(default=True, description="True if served on free tier (actual spend $0.00)")
    cost_badge: str = Field(
        default="ASSUMED_BENCHMARK",
        description="'EXACT_PUBLISHED' if officially priced, or 'ASSUMED_BENCHMARK' if assumed"
    )
    pricing_note: str = Field(
        default="",
        description="Tooltip text explaining whether price is exact published or assumed market benchmark"
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), index=True)
