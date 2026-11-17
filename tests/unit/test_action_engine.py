from datetime import datetime, timedelta, timezone
import uuid
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from backend.core.config import settings
from backend.core.security import create_access_token, hash_password
from backend.db.database import async_session, init_db
from backend.main import app
from backend.models.action_draft import ActionDraft, ActionDraftCreateRequest
from backend.models.enums import (
    ActionDraftStatus,
    ClearanceLevel,
    Role,
    TicketPriority,
    TicketStatus,
    TicketTaxonomy,
)
from backend.models.ticket import Ticket
from backend.models.user import User
from backend.services.integrations.jira_service import (
    DEPARTMENT_PROJECT_MAP,
    build_adf_description,
    jira_service,
)
from backend.services.orchestration.action_engine import (
    ActionInfeasibleError,
    DraftAccessDeniedError,
    DraftExpiredError,
    action_engine,
)
from backend.services.orchestration.action_guard import (
    STANDARD_REFUSAL_MESSAGE,
    action_guard,
)


@pytest.fixture(autouse=True)
async def setup_database():
    await init_db()


@pytest.fixture
async def test_users():
    """Provisions test users across departments for action draft testing."""
    async with async_session() as session:
        uid = uuid.uuid4().hex[:6]

        emp_eng = User(
            name=f"Alice Engineer {uid}",
            username=f"alice_eng_{uid}",
            email=f"alice_{uid}@cortex.test",
            job_title="Software Engineer",
            hashed_password=hash_password("testpass123"),
            role=Role.EMPLOYEE,
            department="Engineering",
            clearance_level=ClearanceLevel.INTERNAL,
            token_version=1,
            is_active=True,
        )
        emp_hr = User(
            name=f"Charlie HR {uid}",
            username=f"charlie_hr_{uid}",
            email=f"charlie_{uid}@cortex.test",
            job_title="HR Associate",
            hashed_password=hash_password("testpass123"),
            role=Role.EMPLOYEE,
            department="HR",
            clearance_level=ClearanceLevel.INTERNAL,
            token_version=1,
            is_active=True,
        )
        admin_user = User(
            name=f"Admin User {uid}",
            username=f"admin_{uid}",
            email=f"admin_{uid}@cortex.test",
            job_title="System Administrator",
            hashed_password=hash_password("testpass123"),
            role=Role.ADMIN,
            department="IT",
            clearance_level=ClearanceLevel.CONFIDENTIAL,
            token_version=1,
            is_active=True,
        )

        session.add(emp_eng)
        session.add(emp_hr)
        session.add(admin_user)
        await session.commit()
        await session.refresh(emp_eng)
        await session.refresh(emp_hr)
        await session.refresh(admin_user)

        return {"eng": emp_eng, "hr": emp_hr, "admin": admin_user}


# =========================================================================
# 1. JiraService Unit Tests
# =========================================================================

def test_jira_service_department_mapping():
    """Verifies department string resolves to correct project key."""
    assert jira_service.resolve_project_key("Engineering") == "ENG"
    assert jira_service.resolve_project_key("it") == "ENG"
    assert jira_service.resolve_project_key("HR") == "HRSD"
    assert jira_service.resolve_project_key("Human Resources") == "HRSD"
    assert jira_service.resolve_project_key("Finance") == "FNA"
    assert jira_service.resolve_project_key("billing") == "FNA"
    # Fallback default
    assert jira_service.resolve_project_key("UnknownDept") in [settings.JIRA_DEFAULT_PROJECT_KEY, "ENG", "ITSD", "KAN"]


def test_jira_service_issue_type_resolution():
    """Verifies taxonomy tier resolves to Jira issue types."""
    available = ["Task", "Bug", "Story"]
    assert jira_service.resolve_issue_type(TicketTaxonomy.INCIDENT, available) == "Bug"
    assert jira_service.resolve_issue_type(TicketTaxonomy.SERVICE_REQUEST, available) == "Task"
    assert jira_service.resolve_issue_type(TicketTaxonomy.INQUIRY, available) == "Task"
    assert jira_service.resolve_issue_type(TicketTaxonomy.DISPUTE, available) == "Task"


def test_adf_description_serialization():
    """Verifies that Atlassian Document Format (ADF) description conforms to Jira v3 spec."""
    adf = build_adf_description(
        title="AWS Bastion Host Access",
        description="Requester needs bastion SSH key for staging cluster debugging.",
        requester_name="Alice Engineer",
        requester_email="alice@cortex.test",
        department="Engineering",
        taxonomy_tier=TicketTaxonomy.SERVICE_REQUEST,
        priority=TicketPriority.HIGH,
        domain_fields={"cluster": "staging-us-east-1", "iam_role": "developer"},
    )
    assert adf["type"] == "doc"
    assert adf["version"] == 1
    assert isinstance(adf["content"], list)
    assert len(adf["content"]) >= 4

    # Verify metadata and domain bullet lists are rendered
    content_types = [block["type"] for block in adf["content"]]
    assert "heading" in content_types
    assert "paragraph" in content_types
    assert "bulletList" in content_types


# =========================================================================
# 2. ActionGuard (Anti-Frivolous Filter) Unit Tests
# =========================================================================

@pytest.mark.asyncio
async def test_action_guard_refuses_frivolous_requests():
    """Verifies non-work requests are cleanly refused per PRD Section 8.3."""
    frivolous_queries = [
        "Order pepperoni pizza for our team party",
        "My coffee is cold, please raise a ticket",
        "Can you fix the traffic on the highway?",
        "Tell me a funny joke to brighten my day",
    ]
    for q in frivolous_queries:
        res = await action_guard.evaluate(q)
        assert res.is_feasible is False
        assert STANDARD_REFUSAL_MESSAGE in res.refusal_reason


@pytest.mark.asyncio
async def test_action_guard_accepts_valid_operational_requests():
    """Verifies enterprise operational requests are accepted and categorized."""
    # Engineering Incident
    res_eng = await action_guard.evaluate("Production Kubernetes cluster auth pods returning 500 internal server error")
    assert res_eng.is_feasible is True
    assert res_eng.department in ["Engineering", "IT"]
    assert res_eng.taxonomy_tier in [TicketTaxonomy.INCIDENT, TicketTaxonomy.SERVICE_REQUEST]

    # HR Service Request
    res_hr = await action_guard.evaluate("Apply for 3 days of medical sick leave starting Monday")
    assert res_hr.is_feasible is True
    assert res_hr.department == "HR"

    # Finance Dispute
    res_fin = await action_guard.evaluate("Expense reimbursement claim was rejected for my client dinner GST tax invoice")
    assert res_fin.is_feasible is True
    assert res_fin.department == "Finance"
    assert res_fin.taxonomy_tier in [TicketTaxonomy.DISPUTE, TicketTaxonomy.SERVICE_REQUEST, TicketTaxonomy.INQUIRY]


# =========================================================================
# 3. ActionEngine State Machine & 15-Minute TTL Unit Tests
# =========================================================================

@pytest.mark.asyncio
async def test_create_action_draft_and_ttl(test_users):
    """Verifies ActionDraft creation with strict 15-minute TTL."""
    user = test_users["eng"]
    async with async_session() as session:
        req = ActionDraftCreateRequest(
            query="Request AWS bastion host static IP access for backend microservice",
            department="Engineering",
            taxonomy_tier=TicketTaxonomy.SERVICE_REQUEST,
            priority=TicketPriority.HIGH,
            domain_fields={"env": "staging"},
        )
        resp = await action_engine.create_draft(session, user, req)

        assert resp.draft_id is not None
        assert resp.user_id == user.id
        assert resp.department == "Engineering"
        assert resp.project_key == "ENG"
        assert resp.taxonomy_tier == TicketTaxonomy.SERVICE_REQUEST
        assert resp.status == ActionDraftStatus.PENDING
        assert resp.is_committed is False
        assert 800 <= resp.ttl_seconds_remaining <= 900  # ~15 minutes (900 seconds)


@pytest.mark.asyncio
async def test_action_guard_rejection_in_engine(test_users):
    """Verifies that frivolous requests throw ActionInfeasibleError during draft creation."""
    user = test_users["eng"]
    async with async_session() as session:
        req = ActionDraftCreateRequest(query="Order large iced latte with oat milk")
        with pytest.raises(ActionInfeasibleError) as exc_info:
            await action_engine.create_draft(session, user, req)
        assert STANDARD_REFUSAL_MESSAGE in str(exc_info.value)


@pytest.mark.asyncio
async def test_draft_tenant_isolation(test_users):
    """Verifies that User B cannot view or confirm User A's draft."""
    user_a = test_users["eng"]
    user_b = test_users["hr"]

    async with async_session() as session:
        req = ActionDraftCreateRequest(query="Provision 32GB RAM developer workstation")
        draft = await action_engine.create_draft(session, user_a, req)

        # User B attempts to access User A's draft
        with pytest.raises(DraftAccessDeniedError):
            await action_engine.get_draft(session, user_b, draft.draft_id)

        # User B attempts to confirm User A's draft
        with pytest.raises(DraftAccessDeniedError):
            await action_engine.confirm_draft(session, user_b, draft.draft_id)


@pytest.mark.asyncio
async def test_draft_15_minute_expiration(test_users):
    """Verifies that an action draft older than 15 minutes is rejected with DraftExpiredError."""
    user = test_users["eng"]
    async with async_session() as session:
        req = ActionDraftCreateRequest(query="Request replacement office ergonomic monitor")
        draft = await action_engine.create_draft(session, user, req)

        # Manually backdate expires_at to 1 minute in the past
        stmt = select(ActionDraft).where(ActionDraft.id == draft.draft_id)
        res = await session.execute(stmt)
        db_draft = res.scalar_one()
        db_draft.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        session.add(db_draft)
        await session.commit()

        # Confirmation must fail with DraftExpiredError
        with pytest.raises(DraftExpiredError) as exc_info:
            await action_engine.confirm_draft(session, user, draft.draft_id)

        assert "expired" in str(exc_info.value).lower()

        # Check status flipped to EXPIRED
        refreshed = await action_engine.get_draft(session, user, draft.draft_id)
        assert refreshed.status == ActionDraftStatus.EXPIRED


@pytest.mark.asyncio
async def test_draft_confirm_idempotency_and_ticket_creation(test_users):
    """Verifies successful confirmation creates Ticket, updates draft, and is idempotent."""
    user = test_users["eng"]
    async with async_session() as session:
        req = ActionDraftCreateRequest(
            query="Database replica lag exceeds 300 seconds on production reporting node",
            department="Engineering",
            taxonomy_tier=TicketTaxonomy.INCIDENT,
            priority=TicketPriority.CRITICAL,
        )
        draft = await action_engine.create_draft(session, user, req)

        # 1. First confirmation -> creates ticket
        ticket, confirm_resp = await action_engine.confirm_draft(session, user, draft.draft_id)
        assert ticket.id is not None
        assert ticket.draft_id == draft.draft_id
        assert ticket.user_id == user.id
        assert ticket.status == TicketStatus.CREATED
        assert ticket.jira_key is not None
        assert confirm_resp.is_committed is True
        assert confirm_resp.status == ActionDraftStatus.CONFIRMED

        # 2. Second confirmation -> Idempotent! Returns same ticket without re-creating
        ticket_dup, confirm_resp_dup = await action_engine.confirm_draft(session, user, draft.draft_id)
        assert ticket_dup.id == ticket.id
        assert ticket_dup.jira_key == ticket.jira_key
        assert confirm_resp_dup.status == ActionDraftStatus.CONFIRMED


@pytest.mark.asyncio
async def test_draft_discard(test_users):
    """Verifies discarding a draft updates its status to DISCARDED."""
    user = test_users["hr"]
    async with async_session() as session:
        req = ActionDraftCreateRequest(query="HR Policy clarification on parental leave insurance coverage")
        draft = await action_engine.create_draft(session, user, req)

        discarded = await action_engine.discard_draft(session, user, draft.draft_id)
        assert discarded.status == ActionDraftStatus.DISCARDED

        # Discarding an already committed draft must fail
        valid_req = ActionDraftCreateRequest(query="VPN access setup")
        draft_valid = await action_engine.create_draft(session, user, valid_req)
        await action_engine.confirm_draft(session, user, draft_valid.draft_id)

        with pytest.raises(Exception):
            await action_engine.discard_draft(session, user, draft_valid.draft_id)


# =========================================================================
# 4. FastAPI Endpoints Integration Tests (/api/v1/actions/*)
# =========================================================================

@pytest.mark.asyncio
async def test_fastapi_actions_endpoints(test_users):
    """Verifies HTTP routes for draft creation, retrieval, confirmation, and discard."""
    user = test_users["eng"]
    token = create_access_token({"sub": str(user.id), "username": user.username, "token_version": 1})
    headers = {"Authorization": f"Bearer {token}"}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Create valid draft -> 201 Created
        resp = await client.post(
            "/api/v1/actions/draft",
            headers=headers,
            json={"query": "Server 500 error in checkout payment gateway microservice"},
        )
        assert resp.status_code == 201
        data = resp.json()
        draft_id = data["draft_id"]
        assert data["department"] in ["Engineering", "IT"]
        assert data["status"] == "PENDING"
        assert data["ttl_seconds_remaining"] > 0

        # 2. Refuse frivolous draft -> 422 Unprocessable Entity
        frivolous_resp = await client.post(
            "/api/v1/actions/draft",
            headers=headers,
            json={"query": "Order hot pizza for everyone"},
        )
        assert frivolous_resp.status_code == 422
        assert STANDARD_REFUSAL_MESSAGE in frivolous_resp.json()["detail"]

        # 3. Retrieve draft by ID -> 200 OK
        get_resp = await client.get(f"/api/v1/actions/{draft_id}", headers=headers)
        assert get_resp.status_code == 200
        assert get_resp.json()["draft_id"] == draft_id

        # 4. Confirm draft -> 200 OK
        confirm_resp = await client.post(f"/api/v1/actions/{draft_id}/confirm", headers=headers)
        assert confirm_resp.status_code == 200
        confirm_data = confirm_resp.json()
        assert confirm_data["ticket_id"] is not None
        assert confirm_data["status"] == "CREATED"
        assert confirm_data["draft"]["is_committed"] is True

        # 5. Create second draft and discard -> 200 OK
        d2 = await client.post(
            "/api/v1/actions/draft",
            headers=headers,
            json={"query": "Request access to Git repository cortex-docs"},
        )
        assert d2.status_code == 201
        d2_id = d2.json()["draft_id"]

        discard_resp = await client.post(f"/api/v1/actions/{d2_id}/discard", headers=headers)
        assert discard_resp.status_code == 200
        assert discard_resp.json()["status"] == "DISCARDED"
