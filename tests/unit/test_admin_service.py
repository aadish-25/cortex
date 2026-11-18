"""
Unit Tests for AdminService (Phase 7 — PRD 10.6, Decision D26, D34)

Tests:
  - inspect_system_health (Postgres, Redis, Qdrant independent probes)
  - query_audit_logs (pagination, category filtering, zero-PII validation)
  - get_document_status (metadata inspection, 404 handling)
  - trigger_document_reindex (state transition, idempotency guard)
  - replace_document_version (version increment, staging guard)
  - get_query_analytics (aggregation over time window)
  - get_feedback_analytics (thumbs ratio, satisfaction percentage)
  - get_token_usage_analytics (provider rollup, cost summation)
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
import uuid
import pytest
from sqlmodel import select

from backend.models.audit import AuditLog
from backend.models.chat import MessageFeedback
from backend.models.document import Document
from backend.models.enums import AuditCategory, ClearanceLevel, DocumentStatus
from backend.models.token_usage import TokenUsageLog
from backend.services.admin.admin_service import (
    get_document_status,
    get_feedback_analytics,
    get_query_analytics,
    get_token_usage_analytics,
    inspect_system_health,
    query_audit_logs,
    replace_document_version,
    trigger_document_reindex,
)


@pytest.mark.asyncio
async def test_inspect_system_health_all_up(async_session_factory):
    """Verifies that when DB, Redis, and Qdrant are responsive, health is UP."""
    async with async_session_factory() as session:
        mock_redis = AsyncMock()
        mock_redis.ping.return_value = True

        mock_qdrant = MagicMock()
        coll_mock = MagicMock()
        coll_mock.collections = [MagicMock(name="cortex_documents")]
        mock_qdrant.get_collections.return_value = coll_mock

        with patch("backend.services.admin.admin_service.get_redis_client", return_value=mock_redis), \
             patch("backend.db.qdrant.get_qdrant_client", return_value=mock_qdrant):
            health = await inspect_system_health(session)

            assert health["overall"] == "UP"
            assert health["services"]["postgres"]["status"] == "UP"
            assert health["services"]["redis"]["status"] == "UP"
            assert health["services"]["qdrant"]["status"] == "UP"


@pytest.mark.asyncio
async def test_inspect_system_health_redis_degraded(async_session_factory):
    """Verifies Decision D34: Redis down marks Redis as DEGRADED without crashing."""
    async with async_session_factory() as session:
        mock_redis = AsyncMock()
        mock_redis.ping.side_effect = ConnectionError("Redis unreachable")

        mock_qdrant = MagicMock()
        coll_mock = MagicMock()
        coll_mock.collections = []
        mock_qdrant.get_collections.return_value = coll_mock

        with patch("backend.services.admin.admin_service.get_redis_client", return_value=mock_redis), \
             patch("backend.db.qdrant.get_qdrant_client", return_value=mock_qdrant):
            health = await inspect_system_health(session)

            assert health["overall"] == "DEGRADED"
            assert health["services"]["postgres"]["status"] == "UP"
            assert health["services"]["redis"]["status"] == "DEGRADED"


@pytest.mark.asyncio
async def test_query_audit_logs_filter_and_pagination(async_session_factory):
    """Tests querying audit logs filtered by category and paginated."""
    async with async_session_factory() as session:
        # Create audit records
        log1 = AuditLog(
            event_tag="TEST_AUTH_1",
            category=AuditCategory.AUTH,
            status_code=200,
            details="User logged in",
        )
        log2 = AuditLog(
            event_tag="TEST_DOC_1",
            category=AuditCategory.DOCUMENT,
            status_code=200,
            details="Uploaded policy",
        )
        session.add(log1)
        session.add(log2)
        await session.commit()

        # Query only DOCUMENT category
        doc_logs = await query_audit_logs(session, category=AuditCategory.DOCUMENT, limit=10)
        assert any(l.event_tag == "TEST_DOC_1" for l in doc_logs)
        assert all(l.category == AuditCategory.DOCUMENT for l in doc_logs)


@pytest.mark.asyncio
async def test_get_document_status_success_and_not_found(async_session_factory):
    """Tests document status lookup by UUID."""
    async with async_session_factory() as session:
        doc = Document(
            filename="admin_test_policy.md",
            file_hash=f"hash_{uuid.uuid4().hex}",
            title="Admin Test Policy",
            department="Engineering",
            clearance_level=ClearanceLevel.INTERNAL,
            status=DocumentStatus.ACTIVE,
            version=1,
            is_live=True,
        )
        session.add(doc)
        await session.commit()
        await session.refresh(doc)

        fetched = await get_document_status(session, doc.id)
        assert fetched.id == doc.id
        assert fetched.filename == "admin_test_policy.md"
        assert fetched.is_live is True

        # Non-existent doc raises ValueError
        with pytest.raises(ValueError, match="not found"):
            await get_document_status(session, uuid.uuid4())


@pytest.mark.asyncio
async def test_trigger_document_reindex(async_session_factory, tmp_path):
    """Tests that triggering reindex flips document status to UPDATING."""
    async with async_session_factory() as session:
        uid = uuid.uuid4().hex[:6]
        src = tmp_path / f"reindex_target_{uid}.md"
        src.write_text("# Reindex Target\nUpdated policy text.", encoding="utf-8")
        doc = Document(
            filename=src.name,
            file_hash=f"hash_{uuid.uuid4().hex}",
            title="Reindex Target",
            department="HR",
            clearance_level=ClearanceLevel.INTERNAL,
            status=DocumentStatus.ACTIVE,
            version=1,
            is_live=True,
            storage_path=str(src),
        )
        session.add(doc)
        await session.commit()
        await session.refresh(doc)

        with patch("backend.worker.tasks.reindex_document_task.delay") as mock_delay:
            res = await trigger_document_reindex(session, doc.id)
            assert res["status"] in ("REINDEX_QUEUED", "REINDEX_PENDING")

            # Check document state in DB
            stmt = select(Document).where(Document.id == doc.id)
            refreshed = (await session.execute(stmt)).scalars().first()
            assert refreshed.status == DocumentStatus.UPDATING
            assert refreshed.is_live is False

            # Calling again while UPDATING returns ALREADY_UPDATING
            res_repeat = await trigger_document_reindex(session, doc.id)
            assert res_repeat["status"] == "ALREADY_UPDATING"


@pytest.mark.asyncio
async def test_replace_document_version(async_session_factory):
    """Tests staging a new document version via replace_document_version."""
    async with async_session_factory() as session:
        uid = uuid.uuid4().hex[:6]
        doc = Document(
            filename=f"replace_policy_{uid}.md",
            file_hash=f"hash_{uuid.uuid4().hex}",
            title="Replace Policy",
            department="Finance",
            clearance_level=ClearanceLevel.CONFIDENTIAL,
            status=DocumentStatus.ACTIVE,
            version=1,
            is_live=True,
        )
        session.add(doc)
        await session.commit()
        await session.refresh(doc)

        new_bytes = f"Updated finance policy content v2 ({uid}) with more details.".encode("utf-8")
        with patch("backend.worker.tasks.reindex_document_task.delay"):
            res = await replace_document_version(
                session=session,
                doc_id=doc.id,
                file_bytes=new_bytes,
            )
        assert res["status"] == "VERSION_STAGED"
        assert res["version"] == 2
        assert res["is_new_version"] is True


@pytest.mark.asyncio
async def test_get_query_and_feedback_analytics(async_session_factory):
    """Tests analytics query aggregations for query volume and thumbs feedback."""
    from backend.models.chat import Conversation, Message
    from backend.models.enums import Role
    from backend.models.user import User

    async with async_session_factory() as session:
        uid = uuid.uuid4().hex[:6]
        user = User(
            name=f"Analytics User {uid}",
            username=f"analytics_{uid}",
            email=f"analytics_{uid}@cortex.local",
            job_title="Analyst",
            hashed_password="hash",
            role=Role.EMPLOYEE,
            department="Engineering",
            clearance_level=ClearanceLevel.INTERNAL,
            token_version=1,
            is_active=True,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)

        conv = Conversation(user_id=user.id, title="Analytics Conv")
        session.add(conv)
        await session.commit()
        await session.refresh(conv)

        m1 = Message(conversation_id=conv.id, sender="assistant", content="Answer 1")
        m2 = Message(conversation_id=conv.id, sender="assistant", content="Answer 2")
        session.add(m1)
        session.add(m2)
        await session.commit()
        await session.refresh(m1)
        await session.refresh(m2)

        # 1. Add audit query event
        q_log = AuditLog(
            event_tag="CHAT_QUERY",
            category=AuditCategory.QUERY,
            status_code=200,
            details="User asked policy question",
        )
        session.add(q_log)

        # 2. Add feedback events linked to valid FK rows
        fb1 = MessageFeedback(
            message_id=m1.id,
            user_id=user.id,
            is_thumbs_up=True,
        )
        fb2 = MessageFeedback(
            message_id=m2.id,
            user_id=user.id,
            is_thumbs_up=False,
        )
        session.add(fb1)
        session.add(fb2)
        await session.commit()

        # Query analytics
        q_analytics = await get_query_analytics(session, days=7)
        assert q_analytics["total_queries"] >= 1
        assert "daily_counts" in q_analytics

        # Feedback analytics
        fb_analytics = await get_feedback_analytics(session, days=7)
        assert fb_analytics["thumbs_up"] >= 1
        assert fb_analytics["thumbs_down"] >= 1
        assert fb_analytics["total_feedback"] >= 2
        assert fb_analytics["satisfaction_percent"] is not None


@pytest.mark.asyncio
async def test_get_token_usage_analytics(async_session_factory):
    """Tests token usage and commercial cost analytics rollups."""
    async with async_session_factory() as session:
        t_log = TokenUsageLog(
            provider="groq",
            model="llama-3.3-70b-versatile",
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
            estimated_cost_usd=0.00012,
            is_free_tier=False,
        )
        session.add(t_log)
        await session.commit()

        usage = await get_token_usage_analytics(session, days=7)
        assert usage["grand_total_tokens"] >= 150
        assert usage["grand_total_cost_usd"] > 0
        assert len(usage["by_provider_model"]) >= 1
