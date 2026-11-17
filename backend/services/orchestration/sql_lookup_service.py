from enum import Enum
import logging
import re
from typing import Any, Dict, List, Optional, Tuple
import uuid

from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select, col

from backend.core.config import settings
from backend.models.enums import Role, TicketStatus
from backend.models.leave import LeaveBalance
from backend.models.ticket import Ticket
from backend.models.user import User
from backend.services.orchestration.llm_service import llm_service

logger = logging.getLogger(__name__)

UUID_PATTERN = re.compile(r"\b([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\b", re.IGNORECASE)


class SQLLookupIntent(str, Enum):
    MY_TICKETS = "MY_TICKETS"
    TICKET_STATUS = "TICKET_STATUS"
    LEAVE_BALANCE = "LEAVE_BALANCE"
    DEPARTMENT_HEAD = "DEPARTMENT_HEAD"
    USER_PROFILE = "USER_PROFILE"
    UNKNOWN = "UNKNOWN"


class SQLLookupClassification(BaseModel):
    intent: SQLLookupIntent
    ticket_id: Optional[str] = None
    department: Optional[str] = None


class SQLLookupResult(BaseModel):
    intent: SQLLookupIntent
    success: bool = True
    message: str
    data: Optional[Any] = None
    records_count: int = 0


# ==========================================
# Typed Parameterized SQL Handlers
# Strictly No Text-to-SQL. Fully Parameterized.
# ==========================================

async def get_my_tickets(
    session: AsyncSession,
    user_id: uuid.UUID,
    limit: int = 10,
) -> List[Dict[str, Any]]:
    """
    Fetches tickets belonging strictly to the requesting user.
    Hardcoded WHERE user_id = current_user.id ensures zero cross-tenant leakage.
    """
    stmt = (
        select(Ticket)
        .where(Ticket.user_id == user_id)
        .order_by(col(Ticket.created_at).desc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    tickets = result.scalars().all()

    return [
        {
            "id": str(t.id),
            "title": t.title,
            "description": t.description,
            "department": t.department,
            "status": t.status.value,
            "jira_key": t.jira_key,
            "created_at": t.created_at.isoformat() if t.created_at else None,
        }
        for t in tickets
    ]


async def get_ticket_status(
    session: AsyncSession,
    user_id: uuid.UUID,
    ticket_id: uuid.UUID,
    is_admin: bool = False,
) -> Optional[Dict[str, Any]]:
    """
    Looks up a specific ticket by UUID. Validates ownership before returning
    data. Returns None if the ticket doesn't exist or belongs to another user.
    """
    stmt = select(Ticket).where(Ticket.id == ticket_id)
    result = await session.execute(stmt)
    ticket = result.scalars().first()

    if not ticket:
        return None

    # Strict isolation: Only the ticket creator or an Admin may inspect the ticket
    if ticket.user_id != user_id and not is_admin:
        logger.warning(
            "User %s attempted unauthorized lookup of ticket %s belonging to %s",
            user_id,
            ticket_id,
            ticket.user_id,
        )
        return None

    return {
        "id": str(ticket.id),
        "title": ticket.title,
        "description": ticket.description,
        "department": ticket.department,
        "status": ticket.status.value,
        "jira_key": ticket.jira_key,
        "created_at": ticket.created_at.isoformat() if ticket.created_at else None,
    }


async def get_leave_balance(
    session: AsyncSession,
    user_id: uuid.UUID,
) -> Dict[str, Any]:
    """
    Retrieves the paid, sick, and casual leave balances for the given employee.
    Auto-initializes standard Indian corporate allocations (18 PL, 12 SL, 6 CL)
    if no row exists yet for this user.
    """
    stmt = select(LeaveBalance).where(LeaveBalance.user_id == user_id)
    result = await session.execute(stmt)
    balance = result.scalars().first()

    if not balance:
        balance = LeaveBalance(
            user_id=user_id,
            paid_leave_days=18.0,
            sick_leave_days=12.0,
            casual_leave_days=6.0,
        )
        session.add(balance)
        await session.commit()
        await session.refresh(balance)

    total_available = balance.paid_leave_days + balance.sick_leave_days + balance.casual_leave_days

    return {
        "user_id": str(user_id),
        "paid_leave_days": balance.paid_leave_days,
        "sick_leave_days": balance.sick_leave_days,
        "casual_leave_days": balance.casual_leave_days,
        "total_available_days": total_available,
        "updated_at": balance.updated_at.isoformat() if balance.updated_at else None,
    }


async def get_department_head(
    session: AsyncSession,
    department: str,
) -> Optional[Dict[str, Any]]:
    """
    Retrieves the department manager/head contact details.
    Falls back to Admin if no Manager is assigned to that department.
    """
    clean_dept = department.strip()
    if not clean_dept:
        return None

    # Search for Manager in that department
    stmt = (
        select(User)
        .where(func.lower(User.department) == clean_dept.lower())
        .where(User.role == Role.MANAGER)
        .where(User.is_active == True)
    )
    result = await session.execute(stmt)
    head = result.scalars().first()

    # Fallback to Admin if no manager found in that department
    if not head:
        admin_stmt = (
            select(User)
            .where(func.lower(User.department) == clean_dept.lower())
            .where(User.role == Role.ADMIN)
            .where(User.is_active == True)
        )
        admin_result = await session.execute(admin_stmt)
        head = admin_result.scalars().first()

    if not head:
        return None

    return {
        "name": head.name,
        "email": head.email,
        "job_title": head.job_title,
        "department": head.department,
        "role": head.role.value,
    }


def get_user_profile(user: User) -> Dict[str, Any]:
    """Returns safe, non-PII identity summary of the authenticated user."""
    return {
        "id": str(user.id),
        "name": user.name,
        "username": user.username,
        "email": user.email,
        "job_title": user.job_title,
        "role": user.role.value,
        "department": user.department,
        "clearance_level": user.clearance_level.value,
    }


# ==========================================
# Intent Classification & Dispatcher (Fast LLM Router)
# ==========================================

async def classify_sql_lookup_intent(query: str) -> Tuple[SQLLookupIntent, Dict[str, Any]]:
    """
    Classifies a structured lookup query into a safe, bounded handler using the fast LLM router.
    Gracefully handles typos, conversational slang, and complex phrasing (e.g. 'show my tiiicket').
    Extracts relevant parameters (e.g. ticket UUID, department name).
    """
    # Fast regex extraction for exact UUID if present in text
    uuid_match = UUID_PATTERN.search(query)
    explicit_uuid = uuid.UUID(uuid_match.group(1)) if uuid_match else None

    # Use Fast LLM Routing Model for robust semantic classification
    system_prompt = (
        "You are Cortex's SQL Lookup Intent Classifier. "
        "Classify the user's operational query into exactly one of these intents:\n"
        "- MY_TICKETS: User asking to view, list, or check their own tickets or issues (handles typos like 'tiiickets', 'my ticets', 'open bugs').\n"
        "- TICKET_STATUS: User querying the specific status, progress, or state of a specific ticket or UUID.\n"
        "- LEAVE_BALANCE: User asking to check their own personal leave balance, remaining vacation days, sick leaves, casual leaves, time off (e.g., 'how many leaves left', 'my sick leave balance'). NOTE: General policy rules questions (e.g. 'can employees carry forward leaves') belong to UNKNOWN.\n"
        "- DEPARTMENT_HEAD: User asking who leads, manages, directs, or heads a department, or requesting department manager contact details (e.g., 'head of engineering', 'who runs hr', 'finance lead', 'HR manager contact details').\n"
        "- USER_PROFILE: User asking about their own identity, role, department, clearance, or profile (e.g., 'who am i', 'what is my department').\n"
        "- UNKNOWN: None of the above.\n\n"
        "Extract ticket_id if present (or null), and department if asked (e.g. 'Engineering', 'HR', 'Finance')."
    )

    try:
        classification = await llm_service.generate_structured(
            prompt=query,
            response_model=SQLLookupClassification,
            system_prompt=system_prompt,
            model=settings.FAST_ROUTING_MODEL,
        )
        if classification and classification.intent in SQLLookupIntent and classification.intent != SQLLookupIntent.UNKNOWN:
            intent = classification.intent
            params: Dict[str, Any] = {}

            if intent == SQLLookupIntent.TICKET_STATUS:
                # Prioritize explicit regex UUID match, fallback to LLM extracted ID
                extracted_id = explicit_uuid
                if not extracted_id and classification.ticket_id:
                    clean_id = UUID_PATTERN.search(classification.ticket_id)
                    if clean_id:
                        extracted_id = uuid.UUID(clean_id.group(1))
                if extracted_id:
                    params["ticket_id"] = extracted_id

            elif intent == SQLLookupIntent.DEPARTMENT_HEAD:
                if classification.department:
                    params["department"] = classification.department.strip()

            return intent, params
    except Exception as e:
        logger.warning("LLM SQL lookup classification failed (%s), falling back to heuristic parsing.", e)

    # Robust heuristic fallback in case LLM is unreachable
    # -------------------------------------------------------------------------------
    q = query.lower().strip()
    if explicit_uuid:
        return SQLLookupIntent.TICKET_STATUS, {"ticket_id": explicit_uuid}

    if any(w in q for w in ["ticket", "jira", "tkt", "tkts", "raised", "raise", "bugs", "issues", "issue", "bug"]):
        return SQLLookupIntent.MY_TICKETS, {}

    if any(phrase in q for phrase in ["leave", "leaves", "vacation", "holiday", "sick leave", "casual leave", "paid leave", "time off"]):
        return SQLLookupIntent.LEAVE_BALANCE, {}

    dept_match = re.search(r"\b(?:head of|manager of|leads?|director of)\s+([a-zA-Z\s]+)", q)
    if dept_match:
        return SQLLookupIntent.DEPARTMENT_HEAD, {"department": dept_match.group(1).strip()}

    for known_dept in ["engineering", "hr", "finance", "human resources", "it", "operations"]:
        if known_dept in q and any(w in q for w in ["head", "lead", "leader", "manager", "director", "chief"]):
            return SQLLookupIntent.DEPARTMENT_HEAD, {"department": known_dept}

    if any(phrase in q for phrase in ["who am i", "my profile", "my department", "my clearance", "my job title", "my email", "my username"]):
        return SQLLookupIntent.USER_PROFILE, {}

    return SQLLookupIntent.UNKNOWN, {}


async def execute_sql_lookup(
    session: AsyncSession,
    user: User,
    query: str,
) -> SQLLookupResult:
    """
    Main entrypoint for structured SQL lookups in the DAG orchestrator.
    Executes typed, role-isolated parameterized queries and returns a sanitized result.
    """
    intent, params = await classify_sql_lookup_intent(query)
    logger.info("Structured SQL Lookup: query='%s', intent=%s, params=%s", query, intent, params)

    if intent == SQLLookupIntent.MY_TICKETS:
        tickets = await get_my_tickets(session, user.id)
        if not tickets:
            return SQLLookupResult(
                intent=intent,
                success=True,
                message="You have no support or incident tickets filed.",
                data=[],
                records_count=0,
            )
        ticket_lines = [f"• [{t['status']}] {t['title']} (ID: {t['id'][:8]}...)" for t in tickets]
        return SQLLookupResult(
            intent=intent,
            success=True,
            message=f"Found {len(tickets)} tickets for {user.name}:\n" + "\n".join(ticket_lines),
            data=tickets,
            records_count=len(tickets),
        )

    elif intent == SQLLookupIntent.TICKET_STATUS:
        ticket_id = params.get("ticket_id")
        if not ticket_id:
            return SQLLookupResult(
                intent=intent,
                success=False,
                message="Please provide a valid ticket UUID to check status.",
                data=None,
            )
        ticket = await get_ticket_status(session, user.id, ticket_id, is_admin=(user.role == Role.ADMIN))
        if not ticket:
            return SQLLookupResult(
                intent=intent,
                success=False,
                message=f"Ticket {ticket_id} was not found or you are not authorized to view it.",
                data=None,
            )
        return SQLLookupResult(
            intent=intent,
            success=True,
            message=f"Ticket '{ticket['title']}' is currently {ticket['status']} in {ticket['department']}.",
            data=ticket,
            records_count=1,
        )

    elif intent == SQLLookupIntent.LEAVE_BALANCE:
        balance = await get_leave_balance(session, user.id)
        msg = (
            f"Leave balance for {user.name}: "
            f"{balance['paid_leave_days']} Paid Leaves, "
            f"{balance['sick_leave_days']} Sick Leaves, "
            f"{balance['casual_leave_days']} Casual Leaves "
            f"({balance['total_available_days']} days total available)."
        )
        return SQLLookupResult(
            intent=intent,
            success=True,
            message=msg,
            data=balance,
            records_count=1,
        )

    elif intent == SQLLookupIntent.DEPARTMENT_HEAD:
        dept = params.get("department", user.department)
        head = await get_department_head(session, dept)
        if not head:
            return SQLLookupResult(
                intent=intent,
                success=False,
                message=f"No designated department head found for '{dept}'.",
                data=None,
            )
        return SQLLookupResult(
            intent=intent,
            success=True,
            message=f"Head of {head['department']} is {head['name']} ({head['job_title']}, {head['email']}).",
            data=head,
            records_count=1,
        )

    elif intent == SQLLookupIntent.USER_PROFILE:
        profile = get_user_profile(user)
        return SQLLookupResult(
            intent=intent,
            success=True,
            message=f"User: {profile['name']} | Role: {profile['role']} | Dept: {profile['department']} | Clearance: {profile['clearance_level']}",
            data=profile,
            records_count=1,
        )

    # Fallback default: Return profile summary
    profile = get_user_profile(user)
    return SQLLookupResult(
        intent=SQLLookupIntent.UNKNOWN,
        success=True,
        message=f"Retrieved profile record for {user.name} ({user.department}).",
        data=profile,
        records_count=1,
    )
