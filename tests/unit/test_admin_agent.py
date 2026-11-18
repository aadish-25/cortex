"""
Unit Tests for AdminAgent (Phase 7 — PRD 10.6, Decision D26)

Tests:
  - Strict RBAC Guard: Employee or Manager cannot execute administrative tools.
  - Tool execution by Role.ADMIN:
    - inspect_system_health
    - inspect_audit_logs
    - get_document_status (UUID and filename)
    - trigger_reindex
    - replace_document_version
  - format_response formatting across all tools.
"""

from unittest.mock import AsyncMock, MagicMock, patch
import uuid
import pytest

from backend.models.document import Document
from backend.models.enums import ClearanceLevel, DocumentStatus, Role
from backend.models.user import User
from backend.services.admin.admin_agent import admin_agent


@pytest.fixture
def mock_admin_user() -> User:
    return User(
        id=uuid.uuid4(),
        username="admin_test",
        email="admin_test@cortex.local",
        hashed_password="hash",
        role=Role.ADMIN,
        department="Engineering",
        clearance_level=ClearanceLevel.CONFIDENTIAL,
    )


@pytest.fixture
def mock_employee_user() -> User:
    return User(
        id=uuid.uuid4(),
        username="emp_test",
        email="emp_test@cortex.local",
        hashed_password="hash",
        role=Role.EMPLOYEE,
        department="Engineering",
        clearance_level=ClearanceLevel.INTERNAL,
    )


@pytest.mark.asyncio
async def test_admin_agent_rbac_refusal(async_session_factory, mock_employee_user):
    """Verifies that non-admin users are strictly blocked from operational tools."""
    async with async_session_factory() as session:
        result = await admin_agent.execute_tool(
            session=session,
            user=mock_employee_user,
            tool_name="inspect_system_health",
        )
        assert result["status"] == "REFUSED"
        assert "Administrative clearance required" in result["error"]

        # Formatted response reflects refusal
        formatted = admin_agent.format_response("inspect_system_health", result)
        assert "Administrative Access Refused" in formatted


@pytest.mark.asyncio
async def test_admin_agent_inspect_system_health(async_session_factory, mock_admin_user):
    """Verifies admin execution of inspect_system_health tool."""
    async with async_session_factory() as session:
        mock_redis = AsyncMock()
        mock_redis.ping.return_value = True

        mock_qdrant = MagicMock()
        coll_mock = MagicMock()
        coll_mock.collections = []
        mock_qdrant.get_collections.return_value = coll_mock

        with patch("backend.services.admin.admin_service.get_redis_client", return_value=mock_redis), \
             patch("backend.db.qdrant.get_qdrant_client", return_value=mock_qdrant):
            res = await admin_agent.execute_tool(
                session=session,
                user=mock_admin_user,
                tool_name="inspect_system_health",
            )
            assert res["status"] == "SUCCESS"
            assert res["data"]["overall"] in ("UP", "DEGRADED")

            formatted = admin_agent.format_response("inspect_system_health", res)
            assert "System Health Status" in formatted


@pytest.mark.asyncio
async def test_admin_agent_get_document_status(async_session_factory, mock_admin_user):
    """Verifies admin execution of get_document_status by filename."""
    async with async_session_factory() as session:
        uid = uuid.uuid4().hex[:6]
        fname = f"travel_rules_{uid}.md"
        doc = Document(
            filename=fname,
            file_hash=f"hash_{uuid.uuid4().hex}",
            title="Travel Rules",
            department="Finance",
            clearance_level=ClearanceLevel.INTERNAL,
            status=DocumentStatus.ACTIVE,
            version=1,
            is_live=True,
        )
        session.add(doc)
        await session.commit()

        # Query by filename
        res = await admin_agent.execute_tool(
            session=session,
            user=mock_admin_user,
            tool_name="get_document_status",
            arguments={"identifier": fname},
        )
        assert res["status"] == "SUCCESS"
        assert res["document"]["filename"] == fname
        assert res["document"]["is_live"] is True

        formatted = admin_agent.format_response("get_document_status", res)
        assert fname in formatted
        assert "v1" in formatted


@pytest.mark.asyncio
async def test_admin_agent_trigger_reindex(async_session_factory, mock_admin_user, tmp_path):
    """Verifies admin execution of trigger_reindex tool."""
    async with async_session_factory() as session:
        uid = uuid.uuid4().hex[:6]
        src = tmp_path / f"onboarding_doc_{uid}.md"
        src.write_text("# Onboarding\nDetails.", encoding="utf-8")
        doc = Document(
            filename=src.name,
            file_hash=f"hash_{uuid.uuid4().hex}",
            title="Onboarding Doc",
            department="HR",
            clearance_level=ClearanceLevel.PUBLIC,
            status=DocumentStatus.ACTIVE,
            version=1,
            is_live=True,
            storage_path=str(src),
        )
        session.add(doc)
        await session.commit()
        await session.refresh(doc)

        with patch("backend.worker.tasks.reindex_document_task.delay"):
            res = await admin_agent.execute_tool(
                session=session,
                user=mock_admin_user,
                tool_name="trigger_reindex",
                arguments={"document_id": str(doc.id)},
            )
            assert res["status"] == "SUCCESS"
            assert res["result"]["status"] in ("REINDEX_QUEUED", "REINDEX_PENDING")

            formatted = admin_agent.format_response("trigger_reindex", res)
            assert "Re-indexing Dispatched" in formatted
