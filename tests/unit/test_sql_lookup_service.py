import uuid
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.database import async_session, init_db
from backend.models.enums import ClearanceLevel, Role, TicketStatus
from backend.models.leave import LeaveBalance
from backend.models.ticket import Ticket
from backend.models.user import User
from backend.services.orchestration.sql_lookup_service import (
    SQLLookupIntent,
    classify_sql_lookup_intent,
    execute_sql_lookup,
    get_department_head,
    get_leave_balance,
    get_my_tickets,
    get_ticket_status,
)


@pytest.fixture(autouse=True)
async def setup_database():
    await init_db()


@pytest.mark.asyncio
async def test_classify_sql_lookup_intent():
    # Leave balance (handles natural conversational variations)
    intent, _ = await classify_sql_lookup_intent("How many leaves do I have left?")
    assert intent == SQLLookupIntent.LEAVE_BALANCE

    intent, _ = await classify_sql_lookup_intent("Check my sick leave and casual leave balance")
    assert intent == SQLLookupIntent.LEAVE_BALANCE

    # Tickets list (handles typos via fast LLM router)
    intent, _ = await classify_sql_lookup_intent("Show my open tickets")
    assert intent == SQLLookupIntent.MY_TICKETS

    intent, _ = await classify_sql_lookup_intent("List all tickets I opened")
    assert intent == SQLLookupIntent.MY_TICKETS

    # Typo tolerance test (e.g. 'tiiicket' / 'tiiickets')
    intent, _ = await classify_sql_lookup_intent("show my tiiickets")
    assert intent == SQLLookupIntent.MY_TICKETS

    # Ticket status with UUID
    test_uuid = uuid.uuid4()
    intent, params = await classify_sql_lookup_intent(f"What is the status of ticket {test_uuid}?")
    assert intent == SQLLookupIntent.TICKET_STATUS
    assert params["ticket_id"] == test_uuid

    # Department head
    intent, params = await classify_sql_lookup_intent("Who is the head of engineering?")
    assert intent == SQLLookupIntent.DEPARTMENT_HEAD
    assert "engineering" in params["department"].lower()

    intent, params = await classify_sql_lookup_intent("HR manager contact details")
    assert intent == SQLLookupIntent.DEPARTMENT_HEAD
    assert "hr" in params["department"].lower()

    # User profile
    intent, _ = await classify_sql_lookup_intent("Who am I and what is my department?")
    assert intent == SQLLookupIntent.USER_PROFILE


@pytest.mark.asyncio
async def test_get_my_tickets_isolation():
    async with async_session() as session:
        user_a = User(
            name="Alice Eng",
            username=f"alice_{uuid.uuid4().hex[:6]}",
            email=f"alice_{uuid.uuid4().hex[:6]}@cortex.internal",
            hashed_password="hash",
            job_title="Engineer",
            role=Role.EMPLOYEE,
            department="Engineering",
            clearance_level=ClearanceLevel.INTERNAL,
        )
        user_b = User(
            name="Bob Finance",
            username=f"bob_{uuid.uuid4().hex[:6]}",
            email=f"bob_{uuid.uuid4().hex[:6]}@cortex.internal",
            hashed_password="hash",
            job_title="Analyst",
            role=Role.EMPLOYEE,
            department="Finance",
            clearance_level=ClearanceLevel.INTERNAL,
        )
        session.add_all([user_a, user_b])
        await session.commit()
        await session.refresh(user_a)
        await session.refresh(user_b)

        # 2 tickets for Alice, 1 ticket for Bob
        t_a1 = Ticket(user_id=user_a.id, title="AWS VPC Access", description="Need access", department="Engineering")
        t_a2 = Ticket(user_id=user_a.id, title="Laptop charger issue", description="Cable damaged", department="IT")
        t_b1 = Ticket(user_id=user_b.id, title="Q3 Expense reimbursement", description="Invoice attached", department="Finance")
        session.add_all([t_a1, t_a2, t_b1])
        await session.commit()

        # Query tickets for Alice
        alice_tickets = await get_my_tickets(session, user_a.id)
        assert len(alice_tickets) == 2
        alice_titles = [t["title"] for t in alice_tickets]
        assert "AWS VPC Access" in alice_titles
        assert "Laptop charger issue" in alice_titles
        assert "Q3 Expense reimbursement" not in alice_titles

        # Query tickets for Bob
        bob_tickets = await get_my_tickets(session, user_b.id)
        assert len(bob_tickets) == 1
        assert bob_tickets[0]["title"] == "Q3 Expense reimbursement"


@pytest.mark.asyncio
async def test_get_ticket_status_isolation():
    async with async_session() as session:
        user_a = User(
            name="Charlie HR",
            username=f"charlie_{uuid.uuid4().hex[:6]}",
            email=f"charlie_{uuid.uuid4().hex[:6]}@cortex.internal",
            hashed_password="hash",
            job_title="HR Associate",
            role=Role.EMPLOYEE,
            department="HR",
            clearance_level=ClearanceLevel.INTERNAL,
        )
        user_b = User(
            name="Diana Eng",
            username=f"diana_{uuid.uuid4().hex[:6]}",
            email=f"diana_{uuid.uuid4().hex[:6]}@cortex.internal",
            hashed_password="hash",
            job_title="Dev",
            role=Role.EMPLOYEE,
            department="Engineering",
            clearance_level=ClearanceLevel.INTERNAL,
        )
        admin_user = User(
            name="Super Admin",
            username=f"admin_{uuid.uuid4().hex[:6]}",
            email=f"admin_{uuid.uuid4().hex[:6]}@cortex.internal",
            hashed_password="hash",
            job_title="Chief Admin",
            role=Role.ADMIN,
            department="IT",
            clearance_level=ClearanceLevel.CONFIDENTIAL,
        )
        session.add_all([user_a, user_b, admin_user])
        await session.commit()
        await session.refresh(user_a)
        await session.refresh(user_b)
        await session.refresh(admin_user)

        ticket_a = Ticket(user_id=user_a.id, title="HR Benefits Query", description="Health policy query", department="HR")
        session.add(ticket_a)
        await session.commit()
        await session.refresh(ticket_a)

        # 1. Owner lookup succeeds
        res_owner = await get_ticket_status(session, user_a.id, ticket_a.id)
        assert res_owner is not None
        assert res_owner["title"] == "HR Benefits Query"

        # 2. Non-owner lookup returns None (Zero-Knowledge Isolation)
        res_intruder = await get_ticket_status(session, user_b.id, ticket_a.id)
        assert res_intruder is None

        # 3. Admin lookup succeeds
        res_admin = await get_ticket_status(session, admin_user.id, ticket_a.id, is_admin=True)
        assert res_admin is not None
        assert res_admin["title"] == "HR Benefits Query"


@pytest.mark.asyncio
async def test_get_leave_balance_auto_initialization():
    async with async_session() as session:
        user = User(
            name="Evan Dev",
            username=f"evan_{uuid.uuid4().hex[:6]}",
            email=f"evan_{uuid.uuid4().hex[:6]}@cortex.internal",
            hashed_password="hash",
            job_title="Software Engineer",
            role=Role.EMPLOYEE,
            department="Engineering",
            clearance_level=ClearanceLevel.INTERNAL,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)

        # First call auto-initializes Indian corporate standard
        bal = await get_leave_balance(session, user.id)
        assert bal["paid_leave_days"] == 18.0
        assert bal["sick_leave_days"] == 12.0
        assert bal["casual_leave_days"] == 6.0
        assert bal["total_available_days"] == 36.0

        # Subsequent call is idempotent and returns identical numbers
        bal_again = await get_leave_balance(session, user.id)
        assert bal_again["total_available_days"] == 36.0


@pytest.mark.asyncio
async def test_get_department_head():
    async with async_session() as session:
        sec_mgr = User(
            name="Priya Sharma",
            username=f"priya_{uuid.uuid4().hex[:6]}",
            email=f"priya_{uuid.uuid4().hex[:6]}@cortex.internal",
            hashed_password="hash",
            job_title="Security Director",
            role=Role.MANAGER,
            department="Security Operations",
            clearance_level=ClearanceLevel.CONFIDENTIAL,
        )
        session.add(sec_mgr)
        await session.commit()

        # Query newly added manager in Security Operations
        head = await get_department_head(session, "Security Operations")
        assert head is not None
        assert head["name"] == "Priya Sharma"
        assert head["role"] == Role.MANAGER.value
        assert head["department"] == "Security Operations"

        # Query existing seeded Engineering department manager
        eng_head = await get_department_head(session, "Engineering")
        assert eng_head is not None
        assert eng_head["role"] == Role.MANAGER.value
        assert eng_head["department"] == "Engineering"

        # Non-existent department
        no_head = await get_department_head(session, "NonExistentDept99")
        assert no_head is None


@pytest.mark.asyncio
async def test_execute_sql_lookup_injection_safety():
    async with async_session() as session:
        user = User(
            name="Grace Hopper",
            username=f"grace_{uuid.uuid4().hex[:6]}",
            email=f"grace_{uuid.uuid4().hex[:6]}@cortex.internal",
            hashed_password="hash",
            job_title="Lead Architect",
            role=Role.EMPLOYEE,
            department="Engineering",
            clearance_level=ClearanceLevel.INTERNAL,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)

        # SQL Injection attempt in query string
        malicious_query = "show tickets ' OR '1'='1' --; DROP TABLE users;"
        result = await execute_sql_lookup(session, user, malicious_query)
        assert result.success is True
        # Treats input safely without SQL syntax error or unintended data exposure
        assert isinstance(result.data, list)


@pytest.mark.asyncio
async def test_execute_sql_lookup_end_to_end():
    async with async_session() as session:
        user = User(
            name="Harish Patel",
            username=f"harish_{uuid.uuid4().hex[:6]}",
            email=f"harish_{uuid.uuid4().hex[:6]}@cortex.internal",
            hashed_password="hash",
            job_title="HR Specialist",
            role=Role.EMPLOYEE,
            department="HR",
            clearance_level=ClearanceLevel.INTERNAL,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)

        # 1. Leave balance query
        res_leave = await execute_sql_lookup(session, user, "What is my current leave balance?")
        assert res_leave.intent == SQLLookupIntent.LEAVE_BALANCE
        assert "18.0 Paid Leaves" in res_leave.message
        assert res_leave.data["total_available_days"] == 36.0

        # 2. My profile query
        res_prof = await execute_sql_lookup(session, user, "Who am I?")
        assert res_prof.intent == SQLLookupIntent.USER_PROFILE
        assert "Harish Patel" in res_prof.message
