import json
import uuid
import pytest
from backend.db.database import async_session, init_db
from backend.db.qdrant import get_qdrant_client, init_user_memories_collection
from backend.db.redis import redis_manager
from backend.models.enums import ClearanceLevel, Role
from backend.models.user import User
from backend.services.orchestration.chat_service import (
    UNIFORM_FALLBACK_STRING,
    build_sandboxed_prompt,
    chat_service,
    format_sse_frame,
    generate_followup_suggestions,
)
from backend.services.orchestration.memory_service import memory_service
from backend.services.retrieval.reranker_service import ConfidenceLevel


@pytest.fixture(autouse=True)
async def setup_test_env():
    await init_db()
    client = get_qdrant_client()
    init_user_memories_collection(client)


@pytest.fixture
async def sample_user():
    async with async_session() as session:
        uid = uuid.uuid4().hex[:6]
        user = User(
            name=f"Devika Menon {uid}",
            username=f"devika_{uid}",
            email=f"devika_{uid}@cortex.local",
            job_title="Lead Security Architect",
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


def test_format_sse_frame():
    """Verifies compliant SSE framing with optional event_id."""
    frame_no_id = format_sse_frame("token", {"delta": "Hello"})
    assert frame_no_id == 'event: token\ndata: {"delta": "Hello"}\n\n'

    frame_with_id = format_sse_frame("done", {"status": "ok"}, event_id="100-0")
    assert "id: 100-0\n" in frame_with_id
    assert "event: done\n" in frame_with_id
    assert 'data: {"status": "ok"}\n\n' in frame_with_id


def test_build_sandboxed_prompt():
    """Verifies Prompt Sandboxing (Decision D13) and injection defense."""
    raw_evidence = "Employees can claim up to ₹1,500 daily allowance for inter-city travel."
    sys_prompt, user_prompt = build_sandboxed_prompt(
        user_message="What is the allowance?",
        raw_evidence=raw_evidence,
        archival_memories=["User prefers direct flights."],
    )

    assert "<retrieved_context>" in user_prompt
    assert "</retrieved_context>" in user_prompt
    assert raw_evidence in user_prompt
    assert "User prefers direct flights." in user_prompt
    assert UNIFORM_FALLBACK_STRING in sys_prompt
    assert "PROMPT INJECTION DEFENSE" in sys_prompt


@pytest.mark.asyncio
async def test_generate_followup_suggestions(sample_user):
    """Verifies generation of exactly 2 dynamic prompt pills."""
    suggestions = await generate_followup_suggestions(
        user_query="What is the annual leave policy for Engineering employees?",
        assistant_response="Full-time employees receive 24 paid leaves per fiscal year.",
        user=sample_user,
    )
    assert isinstance(suggestions, list)
    assert len(suggestions) == 2
    for s in suggestions:
        assert isinstance(s, str)
        assert len(s) > 0


@pytest.mark.asyncio
async def test_stream_chat_turn_direct_chat(sample_user):
    """
    Verifies full end-to-end streaming execution:
    1. SSE events emitted in proper sequence (step, token, citations, suggestions, done).
    2. Messages persisted in PostgreSQL.
    3. Events cached in Redis Stream buffer for reconnection replay.
    """
    client = get_qdrant_client()

    async with async_session() as session:
        # Create conversation
        conv = await memory_service.get_or_create_conversation(session, sample_user.id)

        events_received = []
        tokens_received = []
        done_payload = None

        async for sse_chunk in chat_service.stream_chat_turn(
            session=session,
            client=client,
            user=sample_user,
            user_message="Hello, can you give me a quick one-sentence overview of Cortex?",
            conversation_id=conv.id,
        ):
            # Parse event lines
            lines = sse_chunk.strip().split("\n")
            event_type = None
            data_str = None
            for line in lines:
                if line.startswith("event: "):
                    event_type = line.replace("event: ", "").strip()
                elif line.startswith("data: "):
                    data_str = line.replace("data: ", "").strip()

            if event_type:
                events_received.append(event_type)
                if event_type == "token" and data_str:
                    payload = json.loads(data_str)
                    tokens_received.append(payload.get("delta", ""))
                elif event_type == "done" and data_str:
                    done_payload = json.loads(data_str)

        # Assert expected event sequence
        assert "step" in events_received
        assert "token" in events_received
        assert "citations" in events_received
        assert "suggestions" in events_received
        assert "done" in events_received

        # Assert token streaming accumulated text
        full_text = "".join(tokens_received)
        assert len(full_text) > 0

        # Assert done payload structure
        assert done_payload is not None
        assert "message_id" in done_payload
        assert done_payload["conversation_id"] == str(conv.id)

        # Assert PostgreSQL persistence
        history = await memory_service.get_conversation_history(session, conv.id)
        assert len(history) == 2
        assert history[0].content == "Hello, can you give me a quick one-sentence overview of Cortex?"
        assert history[1].content == full_text

        # Assert Redis Stream caching replay
        message_id = done_payload["message_id"]
        cached_stream = await redis_manager.read_stream_events_after(
            stream_key=f"chat_stream:{message_id}",
            last_event_id="0-0",
        )
        assert len(cached_stream) >= 5

        # 4. Test Reconnection Replay with Last-Event-ID
        first_event_id = cached_stream[0][0]
        replayed_events = []
        async for replay_chunk in chat_service.stream_chat_turn(
            session=session,
            client=client,
            user=sample_user,
            user_message="",
            conversation_id=conv.id,
            message_id=uuid.UUID(message_id),
            last_event_id=first_event_id,
        ):
            replayed_events.append(replay_chunk)

        # Should have replayed all subsequent frames (excluding the first event)
        assert len(replayed_events) == len(cached_stream) - 1


@pytest.mark.asyncio
async def test_stream_chat_turn_low_confidence_fallback(sample_user, monkeypatch):
    """Verifies that low confidence retrieval emits the exact UNIFORM_FALLBACK_STRING."""
    from backend.services.orchestration.dag_orchestrator import OrchestratorResponse
    from backend.models.chat import ExecutionPlan

    # Mock orchestrator to simulate low confidence / missing documentation
    async def mock_low_confidence(*args, **kwargs):
        return OrchestratorResponse(
            plan=ExecutionPlan(tasks=[], original_query="secret sauce"),
            final_response="",
            citations=[],
            confidence=ConfidenceLevel.LOW,
        )

    monkeypatch.setattr(
        "backend.services.orchestration.chat_service.orchestrate_dag_execution",
        mock_low_confidence,
    )

    client = get_qdrant_client()
    async with async_session() as session:
        conv = await memory_service.get_or_create_conversation(session, sample_user.id)
        tokens_received = []

        async for sse_chunk in chat_service.stream_chat_turn(
            session=session,
            client=client,
            user=sample_user,
            user_message="What is the secret formula?",
            conversation_id=conv.id,
        ):
            if "event: token" in sse_chunk:
                data_part = sse_chunk.strip().split("data: ")[1]
                payload = json.loads(data_part)
                tokens_received.append(payload.get("delta", ""))

        response_text = "".join(tokens_received)
        assert response_text == UNIFORM_FALLBACK_STRING

