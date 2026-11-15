import uuid
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.audit import AuditLog
from backend.models.enums import AuditCategory


async def log_audit_event(
    session: AsyncSession,
    event_tag: str,
    category: AuditCategory,
    user_id: Optional[uuid.UUID] = None,
    status_code: int = 200,
    details: Optional[str] = None,
) -> AuditLog:
    """Create an append-only, zero-PII audit record."""
    audit_entry = AuditLog(
        user_id=user_id,
        event_tag=event_tag,
        category=category,
        status_code=status_code,
        details=details,
    )
    session.add(audit_entry)
    await session.commit()
    return audit_entry
