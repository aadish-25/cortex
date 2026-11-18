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
        conv_list = res_convs.json()["items"]
        assert any(c["id"] == str(conv_id) for c in conv_list)

        # 2. Get verbatim messages
        res_msgs = await client.get(
            f"/api/v1/chat/conversations/{conv_id}/messages",
            headers=auth_headers,
        )
        assert res_msgs.status_code == 200
        msgs = res_msgs.json()["items"]
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
    """Verifies canonical memory CRUD, filtering, search, privacy rejection, bulk delete, and cross-user isolation."""
    from unittest.mock import patch
    from qdrant_client import QdrantClient
    from backend.db.qdrant import init_user_memories_collection

    mem_qdrant = QdrantClient(":memory:")
    init_user_memories_collection(mem_qdrant)

    # Create a second user in the same department to verify strict user isolation
    other_uid = uuid.uuid4().hex[:6]
    async with async_session() as session:
        other_user = User(
            name=f"Peer User {other_uid}",
            username=f"peer_{other_uid}",
            email=f"peer_{other_uid}@cortex.local",
            job_title="Engineer",
            hashed_password="hash",
            role=Role.EMPLOYEE,
            department=test_user.department,
            clearance_level=ClearanceLevel.INTERNAL,
            token_version=1,
            is_active=True,
        )
        session.add(other_user)
        await session.commit()
        await session.refresh(other_user)

    other_token = create_access_token(
        data={
            "sub": str(other_user.id),
            "username": other_user.username,
            "role": other_user.role.value,
            "department": other_user.department,
            "clearance_level": other_user.clearance_level.value,
            "token_version": other_user.token_version,
        }
    )
    other_headers = {"Authorization": f"Bearer {other_token}"}

    with patch("backend.routers.memories._safe_qdrant_client", return_value=mem_qdrant):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. Create two memories
            c1 = await client.post(
                "/api/v1/chat/memories",
                json={"text": "Prefers concise dark-mode code blocks.", "category": "preference"},
                headers=auth_headers,
            )
            assert c1.status_code == 201
            m1 = c1.json()
            assert m1["status"] == "active"

            c2 = await client.post(
                "/api/v1/chat/memories",
                json={"text": "Main backend stack is FastAPI and PostgreSQL.", "category": "tech_stack"},
                headers=auth_headers,
            )
            assert c2.status_code == 201
            m2 = c2.json()

            # 2. Secret/credential rejected with 422
            bad = await client.post(
                "/api/v1/chat/memories",
                json={"text": "My API key is sk-proj-1234567890abcdefghij", "category": "general"},
                headers=auth_headers,
            )
            assert bad.status_code == 422

            # 3. List with category filter
            res_cat = await client.get("/api/v1/chat/memories?category=preference", headers=auth_headers)
            assert res_cat.status_code == 200
            assert len(res_cat.json()["items"]) == 1
            assert res_cat.json()["items"][0]["memory_id"] == m1["memory_id"]

            # 4. Detail + Patch + optimistic version conflict (409)
            det = await client.get(f"/api/v1/chat/memories/{m1['memory_id']}", headers=auth_headers)
            assert det.status_code == 200
            patch_ok = await client.patch(
                f"/api/v1/chat/memories/{m1['memory_id']}",
                json={"text": "Prefers very concise answers.", "category": "preference", "expected_version": 1},
                headers=auth_headers,
            )
            assert patch_ok.status_code == 200
            assert patch_ok.json()["version"] == 2

            patch_conflict = await client.patch(
                f"/api/v1/chat/memories/{m1['memory_id']}",
                json={"text": "Stale write", "expected_version": 1},
                headers=auth_headers,
            )
            assert patch_conflict.status_code == 409

            # 5. Cross-user isolation (peer in same department cannot view/delete)
            peer_get = await client.get(f"/api/v1/chat/memories/{m1['memory_id']}", headers=other_headers)
            assert peer_get.status_code == 404
            peer_del = await client.delete(f"/api/v1/chat/memories/{m1['memory_id']}", headers=other_headers)
            assert peer_del.status_code == 404

            # 6. Search endpoint
            search_res = await client.get("/api/v1/chat/memories/search?q=PostgreSQL", headers=auth_headers)
            assert search_res.status_code == 200
            assert any(item["memory_id"] == m2["memory_id"] for item in search_res.json()["items"])

            # 7. Single delete + bulk delete
            del_res = await client.delete(f"/api/v1/chat/memories/{m1['memory_id']}", headers=auth_headers)
            assert del_res.status_code == 200
            assert del_res.json()["status"] == "deleted"

            bulk_res = await client.post(
                "/api/v1/chat/memories/bulk-delete",
                json={"category": "tech_stack"},
                headers=auth_headers,
            )
            assert bulk_res.status_code == 200
            assert bulk_res.json()["deleted_count"] == 1


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
        "document_id": "doc-001",
        "is_live": True,
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
        "document_id": "doc-001",
        "is_live": True,
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
        "document_id": "doc-001",
        "is_live": True,
    }

    mock_client.scroll.side_effect = [([preceding_point], None), ([succeeding_point], None)]

    from backend.core.evidence_tokens import create_evidence_token
    evidence_id = create_evidence_token("target-uuid", test_user.id)
    with patch("backend.db.qdrant.get_qdrant_client", return_value=mock_client):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.get(f"/api/v1/chat/evidence/{evidence_id}", headers=auth_headers)
            assert res.status_code == 200
            data = res.json()
            assert "chunk_id" not in data["target"]
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
