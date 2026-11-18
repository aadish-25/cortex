import json
import uuid
import pytest
from httpx import ASGITransport, AsyncClient

from backend.core.security import create_access_token
from backend.db.database import async_session, init_db
from backend.db.qdrant import get_qdrant_client, init_user_memories_collection
from backend.main import app
from backend.models.chat import Conversation, Message
from backend.models.enums import ClearanceLevel, FeedbackCategory, Role
from backend.models.user import User


@pytest.fixture(autouse=True)
async def setup_test_env():
    await init_db()
    client = get_qdrant_client()
    init_user_memories_collection(client)


@pytest.fixture
async def test_user():
    async with async_session() as session:
        uid = uuid.uuid4().hex[:6]
        user = User(
            name=f"Varun Rao {uid}",
            username=f"varun_{uid}",
            email=f"varun_{uid}@cortex.local",
            job_title="Lead Security Engineer",
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
        return user


@pytest.fixture
def auth_headers(test_user):
    token = create_access_token(
        data={
            "sub": str(test_user.id),
            "username": test_user.username,
            "role": test_user.role.value,
            "department": test_user.department,
            "clearance_level": test_user.clearance_level.value,
            "token_version": test_user.token_version,
        }
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_list_available_models(auth_headers):
    """Verifies that available models are exposed with :free badges (Decision D30)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/api/v1/chat/available-models", headers=auth_headers)
        assert res.status_code == 200
        models = res.json()
        assert len(models) >= 4
        assert any(m["id"] == "groq/qwen/qwen3.8-27b" for m in models)
        assert all(m["badge"] == ":free" for m in models)


@pytest.mark.asyncio
async def test_send_chat_message_streaming(auth_headers, test_user):
    """
    Verifies POST /api/v1/chat/message returns a valid SSE stream
    emitting step, token, citations, suggestions, and done events.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        payload = {
            "message": "Hello, please provide a brief one-line greeting.",
        }
        res = await client.post(
            "/api/v1/chat/message",
            json=payload,
            headers=auth_headers,
        )
        assert res.status_code == 200
        assert "text/event-stream" in res.headers["content-type"]

        # Parse SSE stream chunks
        raw_text = res.text
        lines = raw_text.strip().split("\n")
        events_found = [line.replace("event: ", "").strip() for line in lines if line.startswith("event: ")]

        assert "step" in events_found
        assert "token" in events_found
        assert "suggestions" in events_found
        assert "done" in events_found


@pytest.mark.asyncio
async def test_list_conversations_and_messages(auth_headers, test_user):
    """
    Verifies listing user conversations and retrieving all verbatim messages.
    """
    conv_id = uuid.uuid4()
    msg_id = uuid.uuid4()

    async with async_session() as session:
        conv = Conversation(
            id=conv_id,
            user_id=test_user.id,
            title="Travel Expense Clarification",
            summary="User inquired about meal caps.",
        )
        msg1 = Message(
            id=uuid.uuid4(),
            conversation_id=conv_id,
            sender="user",
            content="What is the meal allowance in Delhi?",
        )
        msg2 = Message(
            id=msg_id,
            conversation_id=conv_id,
            sender="assistant",
            content="The standard meal allowance is ₹2,500 per day.",
            citations=json.dumps(["[Travel Policy v2.0, Section 4]"]),
        )
        session.add(conv)
        session.add(msg1)
        session.add(msg2)
        await session.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. List conversations
        res_convs = await client.get("/api/v1/chat/conversations", headers=auth_headers)
        assert res_convs.status_code == 200
        conv_list = res_convs.json()
        assert any(c["id"] == str(conv_id) for c in conv_list)

        # 2. Get verbatim messages
        res_msgs = await client.get(
            f"/api/v1/chat/conversations/{conv_id}/messages",
            headers=auth_headers,
        )
        assert res_msgs.status_code == 200
        msgs = res_msgs.json()
        assert len(msgs) == 2
        assert msgs[0]["sender"] == "user"
        assert msgs[1]["sender"] == "assistant"
        assert "[Travel Policy v2.0, Section 4]" in msgs[1]["citations"]


@pytest.mark.asyncio
async def test_unauthorized_conversation_access(test_user):
    """Verifies that employees cannot access other employees' conversations."""
    other_conv_id = uuid.uuid4()
    other_uid = uuid.uuid4().hex[:6]

    async with async_session() as session:
        other_user = User(
            name=f"Other User {other_uid}",
            username=f"other_{other_uid}",
            email=f"other_{other_uid}@cortex.local",
            job_title="HR Specialist",
            hashed_password="hash",
            role=Role.EMPLOYEE,
            department="HR",
            clearance_level=ClearanceLevel.INTERNAL,
            token_version=1,
            is_active=True,
        )
        session.add(other_user)
        await session.commit()
        await session.refresh(other_user)

        conv = Conversation(
            id=other_conv_id,
            user_id=other_user.id,
            title="Confidential Executive Planning",
        )
        session.add(conv)
        await session.commit()

    # User token
    token = create_access_token(
        data={
            "sub": str(test_user.id),
            "username": test_user.username,
            "role": test_user.role.value,
            "department": test_user.department,
            "clearance_level": test_user.clearance_level.value,
            "token_version": test_user.token_version,
        }
    )
    headers = {"Authorization": f"Bearer {token}"}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get(
            f"/api/v1/chat/conversations/{other_conv_id}/messages",
            headers=headers,
        )
        assert res.status_code == 404


@pytest.mark.asyncio
async def test_submit_message_feedback(auth_headers, test_user):
    """Verifies recording thumbs up feedback on an assistant message."""
    conv_id = uuid.uuid4()
    msg_id = uuid.uuid4()

    async with async_session() as session:
        conv = Conversation(id=conv_id, user_id=test_user.id, title="Test Conv")
        msg = Message(
            id=msg_id,
            conversation_id=conv_id,
            sender="assistant",
            content="Sample answer",
        )
        session.add(conv)
        session.add(msg)
        await session.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        fb_payload = {
            "message_id": str(msg_id),
            "is_thumbs_up": True,
            "category": FeedbackCategory.ACCURATE.value if hasattr(FeedbackCategory, "ACCURATE") else None,
            "comment": "Accurate and clear answer citing policy.",
        }
        res = await client.post(
            "/api/v1/chat/feedback",
            json=fb_payload,
            headers=auth_headers,
        )
        assert res.status_code == 201
        data = res.json()
        assert data["status"] == "success"
        assert "feedback_id" in data



@pytest.mark.asyncio
async def test_list_and_delete_user_memories(auth_headers, test_user):
    """F5 Test: Verifies listing and deleting personal archival memories."""
    from unittest.mock import patch, AsyncMock
    fake_memories = [
        {"memory_id": "mem-123", "text": "Prefers dark mode", "category": "PREFERENCE", "created_at": "2026-01-01T00:00:00Z"},
    ]
    with patch(
        "backend.services.orchestration.episodic_memory_service.episodic_memory_service.list_memories",
        new=AsyncMock(return_value=fake_memories),
    ), patch(
        "backend.services.orchestration.episodic_memory_service.episodic_memory_service.delete_memory",
        new=AsyncMock(return_value=True),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.get("/api/v1/chat/memories", headers=auth_headers)
            assert res.status_code == 200
            data = res.json()
            assert len(data) == 1
            assert data[0]["memory_id"] == "mem-123"
            assert data[0]["text"] == "Prefers dark mode"

            del_res = await client.delete("/api/v1/chat/memories/mem-123", headers=auth_headers)
            assert del_res.status_code == 200
            assert del_res.json()["deleted"] is True


@pytest.mark.asyncio
async def test_get_evidence_context_not_found(auth_headers):
    """F8 Test: Verifies 404 response when requested chunk is not found in Qdrant."""
    from unittest.mock import patch, MagicMock
    mock_client = MagicMock()
    mock_client.retrieve.return_value = []
    with patch("backend.db.qdrant.get_qdrant_client", return_value=mock_client):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.get("/api/v1/chat/evidence/nonexistent-chunk-id", headers=auth_headers)
            assert res.status_code == 404


@pytest.mark.asyncio
async def test_get_evidence_context_success(auth_headers, test_user):
    """F8 Test: Verifies 3-chunk sandwich context retrieval with RBAC compliance."""
    from unittest.mock import patch, MagicMock
    from qdrant_client import models as qmodels

    target_point = MagicMock()
    target_point.id = "target-uuid"
    target_point.payload = {
        "text": "Target cited clause.",
        "citation": "Policy A, Section 1",
        "section": "Section 1",
        "department": test_user.department,
        "clearance_level": "Internal",
        "doc_id": "doc-001",
        "chunk_index": 2,
    }

    mock_client = MagicMock()
    mock_client.retrieve.return_value = [target_point]

    preceding_point = MagicMock()
    preceding_point.id = "prec-uuid"
    preceding_point.payload = {
        "text": "Preceding context clause.",
        "citation": "Policy A, Section 1",
        "section": "Section 1",
        "department": test_user.department,
        "clearance_level": "Internal",
        "doc_id": "doc-001",
        "chunk_index": 1,
    }
    succeeding_point = MagicMock()
    succeeding_point.id = "succ-uuid"
    succeeding_point.payload = {
        "text": "Succeeding context clause.",
        "citation": "Policy A, Section 2",
        "section": "Section 2",
        "department": test_user.department,
        "clearance_level": "Internal",
        "doc_id": "doc-001",
        "chunk_index": 3,
    }

    mock_client.scroll.return_value = ([preceding_point, succeeding_point], None)

    with patch("backend.db.qdrant.get_qdrant_client", return_value=mock_client):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.get("/api/v1/chat/evidence/target-uuid", headers=auth_headers)
            assert res.status_code == 200
            data = res.json()
            assert data["target"]["chunk_id"] == "target-uuid"
            assert data["target"]["text"] == "Target cited clause."
            assert data["preceding"]["text"] == "Preceding context clause."
            assert data["succeeding"]["text"] == "Succeeding context clause."


@pytest.mark.asyncio
async def test_send_chat_message_with_client_message_id(auth_headers, test_user):
    """F9 Test: Verifies POST /api/v1/chat/message accepts a client-provided message_id."""
    client_msg_id = str(uuid.uuid4())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.post(
            "/api/v1/chat/message",
            json={
                "message": "Hello with custom id",
                "message_id": client_msg_id,
            },
            headers=auth_headers,
        )
        assert res.status_code == 200
        assert "text/event-stream" in res.headers["content-type"]
