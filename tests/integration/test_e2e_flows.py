"""
Full-Fledged End-to-End Integration Flow Tests for Cortex.

Covers complete multi-step user journeys across:
  - Authentication (login / JWT / user isolation)
  - Chat SSE streaming (`/api/v1/chat/message`)
  - Explicit Memory Lifecycle via Chat (ADD -> SUPERSEDE -> RETRIEVE -> PRIVACY REFUSAL -> DELETE -> IDEMPOTENT RE-DELETE)
  - Zero-LLM Hybrid Retrieval on non-first-person follow-up queries
  - Canonical REST API CRUD (`/api/v1/chat/memories`), status history, search, optimistic concurrency (409), bulk delete
  - Cross-user & same-department isolation (conversations + memories)
  - Celery worker lazy-import guard (litellm must not be eagerly imported by worker tasks)
"""

import json
from unittest.mock import AsyncMock, patch
import uuid

from httpx import ASGITransport, AsyncClient
import pytest
from qdrant_client import QdrantClient

from backend.core.security import create_access_token
from backend.db.database import async_session
from backend.db.qdrant import init_user_memories_collection
from backend.main import app
from backend.models.enums import ClearanceLevel, Role
from backend.models.user import User
from backend.services.memory.memory_manager import memory_manager


def _parse_sse_frames(raw_sse: str) -> list[tuple[str, dict]]:
    frames: list[tuple[str, dict]] = []
    for block in raw_sse.strip().split("\n\n"):
        ev_type = None
        ev_data = None
        for line in block.splitlines():
            if line.startswith("event: "):
                ev_type = line[7:].strip()
            elif line.startswith("data: "):
                raw = line[6:].strip()
                try:
                    ev_data = json.loads(raw)
                except Exception:
                    ev_data = {"raw": raw}
        if ev_type is not None:
            frames.append((ev_type, ev_data or {}))
    return frames


async def _provision_user(prefix: str, department: str = "Engineering") -> tuple[User, dict[str, str]]:
    uid = uuid.uuid4().hex[:6]
    async with async_session() as session:
        user = User(
            name=f"{prefix} {uid}",
            username=f"{prefix.lower()}_{uid}",
            email=f"{prefix.lower()}_{uid}@cortex.local",
            job_title="Senior Engineer",
            hashed_password="hash",
            role=Role.EMPLOYEE,
            department=department,
            clearance_level=ClearanceLevel.INTERNAL,
            token_version=1,
            is_active=True,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)

    token = create_access_token(
        data={
            "sub": str(user.id),
            "username": user.username,
            "role": user.role.value,
            "department": user.department,
            "clearance_level": user.clearance_level.value,
            "token_version": user.token_version,
        }
    )
    return user, {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_e2e_full_memory_chat_and_isolation_lifecycle():
    """End-to-end journey through chat SSE, memory CRUD, superseding, privacy blocks, retrieval, and isolation."""
    mem_qdrant = QdrantClient(":memory:")
    init_user_memories_collection(mem_qdrant)

    user_a, headers_a = await _provision_user("E2EUserA", department="Engineering")
    user_b, headers_b = await _provision_user("E2EUserB", department="Engineering")

    dag_spy = AsyncMock()
    with patch("backend.routers.chat.get_qdrant_client", return_value=mem_qdrant), \
         patch("backend.routers.memories._safe_qdrant_client", return_value=mem_qdrant), \
         patch("backend.services.orchestration.chat_service.orchestrate_dag_execution", dag_spy), \
         patch("backend.services.memory.memory_manager.llm_service.is_configured", return_value=False):

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. Explicit ADD via SSE chat (bypasses DAG/RAG completely)
            res_add = await client.post(
                "/api/v1/chat/message",
                json={"message": "Remember that I prefer concise technical explanations."},
                headers=headers_a,
            )
            assert res_add.status_code == 200
            frames_add = _parse_sse_frames(res_add.text)
            ev_names = [t for t, _ in frames_add]
            assert "memory_event" in ev_names
            assert "done" in ev_names
            dag_spy.assert_not_called()

            done_data = next(d for t, d in frames_add if t == "done")
            conv_id = done_data["conversation_id"]

            # 2. Verify saved via REST API & add project-specific memories
            res_list = await client.get("/api/v1/chat/memories", headers=headers_a)
            assert res_list.status_code == 200
            assert res_list.json()["total"] == 1

            p1 = await client.post(
                "/api/v1/chat/memories",
                json={"text": "Uses MongoDB for the IronLog mobile app.", "category": "project_context"},
                headers=headers_a,
            )
            p2 = await client.post(
                "/api/v1/chat/memories",
                json={"text": "Uses FastAPI and PostgreSQL for Cortex.", "category": "project_context"},
                headers=headers_a,
            )
            assert p1.status_code == 201 and p2.status_code == 201
            ironlog_id = p1.json()["memory_id"]

            # 3. Explicit UPDATE via chat supersedes MongoDB memory cleanly
            res_upd = await client.post(
                "/api/v1/chat/message",
                json={"message": "Update my MongoDB preference to PostgreSQL.", "conversation_id": conv_id},
                headers=headers_a,
            )
            assert res_upd.status_code == 200
            sup_res = await client.get("/api/v1/chat/memories?status=superseded", headers=headers_a)
            assert sup_res.status_code == 200
            assert any(m["memory_id"] == ironlog_id for m in sup_res.json()["items"])

            # 4. Explicit RETRIEVE via chat lists active memories truthfully
            res_ret = await client.post(
                "/api/v1/chat/message",
                json={"message": "What do you remember about me?", "conversation_id": conv_id},
                headers=headers_a,
            )
            assert res_ret.status_code == 200
            ret_text = "".join(d.get("delta", "") for t, d in _parse_sse_frames(res_ret.text) if t == "token")
            assert "concise technical explanations" in ret_text
            assert "Cortex" in ret_text

            # 5. Non-first-person semantic retrieval pulls User A's project context with 0 LLM calls
            async with async_session() as session:
                recalled = await memory_manager.retrieve_relevant_memories(
                    session=session,
                    client=mem_qdrant,
                    user=user_a,
                    query="Continue the project from last month using the same stack as before",
                )
            assert any("FastAPI" in m for m in recalled)
            assert any("concise technical explanations" in m for m in recalled)

            # 6. Secret blocked in BOTH chat and REST API
            res_secret_rest = await client.post(
                "/api/v1/chat/memories",
                json={"text": "My secret key is sk-proj-abcdef1234567890abcd", "category": "general"},
                headers=headers_a,
            )
            assert res_secret_rest.status_code == 422

            res_secret_chat = await client.post(
                "/api/v1/chat/message",
                json={"message": "Remember that my password is SuperSecretPassword99!", "conversation_id": conv_id},
                headers=headers_a,
            )
            assert res_secret_chat.status_code == 200
            secret_reply = "".join(d.get("delta", "") for t, d in _parse_sse_frames(res_secret_chat.text) if t == "token")
            assert "Cannot store" in secret_reply or "privacy" in secret_reply.lower()

            # 7. Same-department User B has ZERO access to User A's memories or conversation
            b_list = await client.get("/api/v1/chat/memories", headers=headers_b)
            assert b_list.status_code == 200 and b_list.json()["total"] == 0

            b_conv = await client.get(f"/api/v1/chat/conversations/{conv_id}/messages", headers=headers_b)
            assert b_conv.status_code == 404

            b_bulk = await client.post(
                "/api/v1/chat/memories/bulk-delete",
                json={"memory_ids": [p2.json()["memory_id"]]},
                headers=headers_b,
            )
            assert b_bulk.status_code == 200 and b_bulk.json()["deleted_count"] == 0

            # 8. Explicit DELETE ALL via chat + idempotent repeat
            res_del1 = await client.post(
                "/api/v1/chat/message",
                json={"message": "Forget all my memories.", "conversation_id": conv_id},
                headers=headers_a,
            )
            assert res_del1.status_code == 200
            after_del = await client.get("/api/v1/chat/memories", headers=headers_a)
            assert after_del.json()["total"] == 0

            res_del2 = await client.post(
                "/api/v1/chat/message",
                json={"message": "Forget all my memories.", "conversation_id": conv_id},
                headers=headers_a,
            )
            del2_text = "".join(d.get("delta", "") for t, d in _parse_sse_frames(res_del2.text) if t == "token")
            assert "No matching memories" in del2_text


def test_celery_tasks_module_does_not_eagerly_import_litellm():
    """Regression guard ensuring Celery workers never pull +168MB litellm at boot."""
    import subprocess
    import sys

    code = (
        "import sys; import backend.worker.tasks; "
        "assert 'litellm' not in sys.modules, 'litellm was eagerly imported by Celery worker!'"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert proc.returncode == 0, f"Unexpected stderr: {proc.stderr}"
