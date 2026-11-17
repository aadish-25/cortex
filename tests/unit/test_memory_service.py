import uuid
import pytest
from backend.db.database import async_session, init_db
from backend.models.chat import ChatRole
from backend.models.enums import ClearanceLevel, Role
from backend.models.user import User
from backend.services.orchestration.memory_service import (
    MAX_CONVERSATION_TOKENS,
    estimate_token_count,
    memory_service,
)


@pytest.fixture(autouse=True)
async def setup_database():
    await init_db()


@pytest.fixture
async def sample_user():
    async with async_session() as session:
        uid = uuid.uuid4().hex[:6]
        user = User(
            name=f"Rohan Verma {uid}",
            username=f"rohan_{uid}",
            email=f"rohan_{uid}@cortex.local",
            job_title="Lead DevOps Engineer",
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


@pytest.mark.asyncio
async def test_get_or_create_conversation(sample_user):
    """Verifies provisioning a new conversation and retrieving an existing one."""
    async with async_session() as session:
        # Create brand new conversation
        conv1 = await memory_service.get_or_create_conversation(session, sample_user.id)
        assert conv1.id is not None
        assert conv1.user_id == sample_user.id
        assert conv1.title == "New Conversation"

        # Retrieve the exact same conversation
        conv_retrieved = await memory_service.get_or_create_conversation(
            session, sample_user.id, conversation_id=conv1.id
        )
        assert conv_retrieved.id == conv1.id


@pytest.mark.asyncio
async def test_append_message_and_retrieve_history(sample_user):
    """Verifies persisting user and assistant messages with citation JSON."""
    async with async_session() as session:
        conv = await memory_service.get_or_create_conversation(session, sample_user.id)

        # 1. User message
        m1 = await memory_service.append_message(
            session=session,
            conversation_id=conv.id,
            sender="user",
            content="What is the meal allowance for travel?",
        )
        assert m1.sender == "user"
        assert m1.citations is None

        # 2. Assistant message with citations
        cites = ["[Travel Policy, Section: Meal Allowance, p. 2]"]
        m2 = await memory_service.append_message(
            session=session,
            conversation_id=conv.id,
            sender="assistant",
            content="The standard meal allowance is ₹2,500 per day.",
            citations=cites,
        )
        assert m2.sender == "assistant"
        assert cites[0] in m2.citations

        # 3. Retrieve history
        history = await memory_service.get_conversation_history(session, conv.id)
        assert len(history) == 2
        assert history[0].role == ChatRole.USER
        assert history[0].content == "What is the meal allowance for travel?"
        assert history[1].role == ChatRole.ASSISTANT
        assert "₹2,500" in history[1].content


def test_build_user_context_block(sample_user):
    """Verifies that authenticated employee facts are injected into context."""
    block = memory_service.build_user_context_block(sample_user)
    assert sample_user.name in block
    assert sample_user.email in block
    assert "Engineering" in block
    assert "Employee" in block
    assert "Internal" in block


def test_estimate_token_count():
    """Verifies fast character-based token estimation."""
    text = "Hello world! This is a test prompt for token estimation."
    tokens = estimate_token_count(text)
    assert 10 <= tokens <= 20
    assert estimate_token_count("") == 0


@pytest.mark.asyncio
async def test_prepare_context_window_short_history(sample_user):
    """Verifies that short conversations remain intact without summarization."""
    async with async_session() as session:
        conv = await memory_service.get_or_create_conversation(session, sample_user.id)
        await memory_service.append_message(session, conv.id, "user", "Hello")
        await memory_service.append_message(session, conv.id, "assistant", "Hi! How can I help?")

        window, is_near_ceiling = await memory_service.prepare_context_window(
            session, conv.id, sample_user
        )
        assert len(window) == 2
        assert is_near_ceiling is False
        assert window[0].content == "Hello"
        assert window[1].content == "Hi! How can I help?"


@pytest.mark.asyncio
async def test_auto_title_conversation(sample_user):
    """Verifies auto-generating a concise 3-5 word title on Turn 1."""
    async with async_session() as session:
        conv = await memory_service.get_or_create_conversation(session, sample_user.id)
        assert conv.title == "New Conversation"

        await memory_service.auto_title_conversation_if_needed(
            session=session,
            conversation=conv,
            first_user_prompt="How do I configure PostgreSQL connection pool settings for Kubernetes staging?",
        )
        assert conv.title != "New Conversation"
        assert len(conv.title) > 0
