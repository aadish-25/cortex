import uuid
import pytest
from backend.db.database import async_session, init_db
from backend.db.qdrant import get_qdrant_client, init_user_memories_collection
from backend.models.enums import ClearanceLevel, Role
from backend.models.user import User
from backend.services.orchestration.episodic_memory_service import episodic_memory_service


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
            name=f"Ananya Sharma {uid}",
            username=f"ananya_{uid}",
            email=f"ananya_{uid}@cortex.local",
            job_title="Senior Platform Engineer",
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
async def test_extract_and_store_memories_deduplication(sample_user):
    """
    Verifies that personal user facts are autonomously extracted from dialogue,
    vectorized into Qdrant, and deduplicated against repeated facts.
    """
    client = get_qdrant_client()

    # 1. First fact extraction turn
    msg1 = "Hi, my name is Ananya and I work primarily out of the Bangalore tech campus."
    facts1 = await episodic_memory_service.extract_and_store_memories(
        client=client,
        user_id=sample_user.id,
        user_message=msg1,
    )
    assert len(facts1) >= 1
    assert any("Bangalore" in f or "campus" in f or "office" in f or "work" in f for f in facts1)

    # 2. Semantic Search on-demand
    memories = await episodic_memory_service.search_memories(
        client=client,
        user_id=sample_user.id,
        query="Which office does the user work from?",
    )
    assert len(memories) >= 1
    assert any("Bangalore" in m for m in memories)

    # 3. Deduplication: user says the same fact again
    msg2 = "As I mentioned, I am based out of the Bangalore office."
    facts2 = await episodic_memory_service.extract_and_store_memories(
        client=client,
        user_id=sample_user.id,
        user_message=msg2,
    )
    # Deduplication threshold (0.85) should prevent re-inserting the same fact
    assert len(facts2) == 0

    # 4. List memories
    all_memories = await episodic_memory_service.list_memories(client=client, user_id=sample_user.id)
    assert len(all_memories) == len(facts1)

    # 5. Delete memory for privacy compliance
    mem_id = all_memories[0]["id"]
    deleted = await episodic_memory_service.delete_memory(client=client, user_id=sample_user.id, memory_id=mem_id)
    assert deleted is True

    # Confirm deletion
    post_delete = await episodic_memory_service.list_memories(client=client, user_id=sample_user.id)
    assert len(post_delete) == len(all_memories) - 1
    assert not any(p["id"] == mem_id for p in post_delete)
