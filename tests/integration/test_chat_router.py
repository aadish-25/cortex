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
