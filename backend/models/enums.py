from enum import Enum


class Role(str, Enum):
    ADMIN = "Admin"
    MANAGER = "Manager"
    EMPLOYEE = "Employee"


class ClearanceLevel(str, Enum):
    PUBLIC = "Public"
    INTERNAL = "Internal"
    CONFIDENTIAL = "Confidential"


CLEARANCE_RANK = {
    ClearanceLevel.PUBLIC: 1,
    ClearanceLevel.INTERNAL: 2,
    ClearanceLevel.CONFIDENTIAL: 3,
}


def is_clearance_sufficient(user_clearance: ClearanceLevel, required_clearance: ClearanceLevel) -> bool:
    return CLEARANCE_RANK.get(user_clearance, 0) >= CLEARANCE_RANK.get(required_clearance, 0)


class DocumentStatus(str, Enum):
    ACTIVE = "ACTIVE"
    UPDATING = "UPDATING"
    UPDATE_FAILED = "UPDATE_FAILED"


class TicketStatus(str, Enum):
    OUTBOX_PENDING = "OUTBOX_PENDING"
    CREATED = "CREATED"
    FAILED = "FAILED"


class FeedbackCategory(str, Enum):
    INACCURATE = "INACCURATE"
    MISSING_CONTEXT = "MISSING_CONTEXT"
    OUTDATED = "OUTDATED"
    OTHER = "OTHER"


class AuditCategory(str, Enum):
    AUTH = "AUTH"
    DOCUMENT = "DOCUMENT"
    QUERY = "QUERY"
    WORKFLOW = "WORKFLOW"
    ADMIN = "ADMIN"
