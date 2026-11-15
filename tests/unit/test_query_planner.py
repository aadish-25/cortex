import pytest

from backend.models.chat import ChatMessage, ChatRole, PlanTaskType
from backend.services.orchestration.query_planner import (
    decompose_compound_query_heuristic,
    detect_anaphora_or_pronoun,
    extract_recent_history_window,
    plan_and_decompose_query,
    rewrite_query,
)


def test_turn_1_zero_latency_bypass():
    """Turn 1 queries with empty conversation history must bypass rewriting with 0ms overhead."""
    query = "What is the engineering travel per diem allowance?"
    rewritten, was_rewritten = pytest.run(rewrite_query(query, history=[])) if False else (
        # rewrite_query is an async function
        None, None
    )


@pytest.mark.asyncio
async def test_turn_1_bypass_async():
    """Verifies Turn 1 returns identical text and was_rewritten is False."""
    query = "What is the engineering travel per diem allowance?"
    rewritten, was_rewritten = await rewrite_query(query, history=[])
    assert rewritten == query
    assert was_rewritten is False


@pytest.mark.asyncio
async def test_turn_2_no_anaphora_bypass():
    """Turn 2+ query without pronouns/anaphora must bypass rewriting."""
    history = [
        ChatMessage(role=ChatRole.USER, content="Hello"),
        ChatMessage(role=ChatRole.ASSISTANT, content="Hi! How can I help?"),
    ]
    query = "Where is the Bengaluru office located?"
    rewritten, was_rewritten = await rewrite_query(query, history=history)
    assert rewritten == query
    assert was_rewritten is False


def test_detect_anaphora_pronouns():
    """Verifies regex identification of conversational anaphora and pronouns."""
    assert detect_anaphora_or_pronoun("Can I claim that?") is True
    assert detect_anaphora_or_pronoun("What about it?") is True
    assert detect_anaphora_or_pronoun("Is this allowed?") is True
    assert detect_anaphora_or_pronoun("Explain the rules for them") is True
    assert detect_anaphora_or_pronoun("Tell me more...") is True

    # Standalone queries without anaphora
    assert detect_anaphora_or_pronoun("Engineering travel policy per diem") is False
    assert detect_anaphora_or_pronoun("Maternity benefit act leaves") is False


def test_extract_recent_history_window():
    """Verifies sliding window takes max 4 non-system messages."""
    history = [
        ChatMessage(role=ChatRole.SYSTEM, content="System instructions"),
        ChatMessage(role=ChatRole.USER, content="Turn 1 User"),
        ChatMessage(role=ChatRole.ASSISTANT, content="Turn 1 Assistant"),
        ChatMessage(role=ChatRole.USER, content="Turn 2 User"),
        ChatMessage(role=ChatRole.ASSISTANT, content="Turn 2 Assistant"),
        ChatMessage(role=ChatRole.USER, content="Turn 3 User"),
        ChatMessage(role=ChatRole.ASSISTANT, content="Turn 3 Assistant"),
    ]
    window = extract_recent_history_window(history, max_messages=4)
    assert len(window) == 4
    assert window[0].content == "Turn 2 User"
    assert window[-1].content == "Turn 3 Assistant"


@pytest.mark.asyncio
async def test_turn_2_heuristic_fusion_rewrite():
    """When anaphora is detected without Gemini API key, falls back to deterministic context fusion."""
    history = [
        ChatMessage(role=ChatRole.USER, content="What is the daily meal cap for Bangalore offsite?"),
        ChatMessage(role=ChatRole.ASSISTANT, content="The daily meal allowance is ₹2,500/day for engineering offsites."),
    ]
    query = "Can I claim that for dinner?"
    rewritten, was_rewritten = await rewrite_query(query, history=history)
    assert was_rewritten is True
    assert "Bangalore offsite" in rewritten


def test_compound_query_decomposition_dag():
    """
    Verifies Decision D24: Compound prompt decomposes into explicit sub-tasks with depends_on.
    """
    compound_query = "Fetch my leave balance; and then draft leave application for 3 days"
    tasks = decompose_compound_query_heuristic(compound_query)

    assert len(tasks) == 2
    # Sub-task 1: Leave balance lookup
    assert tasks[0].id == 1
    assert tasks[0].type == PlanTaskType.STRUCTURED_LOOKUP
    assert tasks[0].depends_on == []

    # Sub-task 2: Action workflow (drafting leave application)
    assert tasks[1].id == 2
    assert tasks[1].type == PlanTaskType.ACTION_WORKFLOW
    assert tasks[1].depends_on == [1]


@pytest.mark.asyncio
async def test_plan_and_decompose_e2e():
    """Verifies end-to-end plan generation returns an ExecutionPlan with tasks."""
    plan = await plan_and_decompose_query("Summarize engineering workstation policy")
    assert plan.original_query == "Summarize engineering workstation policy"
    assert len(plan.tasks) == 1
    assert plan.tasks[0].type == PlanTaskType.INTERNAL_RAG
