from datetime import datetime, timezone
from typing import ClassVar, Optional
import uuid
from sqlmodel import Field, SQLModel


class LeaveBalance(SQLModel, table=True):
    """
    Tracks annual leave allocations, sick days, and casual leaves per employee.
    Used for bounded SQL lookups and HR self-service queries.
    """
    __tablename__: ClassVar[str] = "leave_balances"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="users.id", index=True, unique=True)
    paid_leave_days: float = Field(default=18.0, description="Annual earned/privilege leaves")
    sick_leave_days: float = Field(default=12.0, description="Annual medical/sick leaves")
    casual_leave_days: float = Field(default=6.0, description="Annual casual leaves")
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
