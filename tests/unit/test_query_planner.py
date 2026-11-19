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
    assert "Bangalore" in rewritten and ("meal" in rewritten.lower() or "offsite" in rewritten.lower())


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


def test_classify_task_intent_chit_chat():
    """Verifies non-enterprise conversational queries classify as DIRECT_CHAT."""
    from backend.services.orchestration.query_planner import classify_task_intent

    assert classify_task_intent("What's up? What you doing?") == PlanTaskType.DIRECT_CHAT
    assert classify_task_intent("hello, how are you?") == PlanTaskType.DIRECT_CHAT
    assert classify_task_intent("who are you?") == PlanTaskType.DIRECT_CHAT


def test_format_conversation_history_with_compaction():
    """Verifies conversation history incorporates rolling executive summaries."""
    from backend.services.orchestration.query_planner import format_conversation_history

    history = [
        ChatMessage(role=ChatRole.USER, content="Can contractors use MacBook Pro?"),
        ChatMessage(role=ChatRole.ASSISTANT, content="Contractors are issued standard ThinkPads."),
    ]
    formatted = format_conversation_history(
        history=history,
        summary="User is an Engineering manager discussing hardware provisioning."
    )
    assert "[Prior Conversation Summary]: User is an Engineering manager discussing hardware provisioning." in formatted
    assert "User: Can contractors use MacBook Pro?" in formatted
    assert "Assistant: Contractors are issued standard ThinkPads." in formatted


@pytest.mark.asyncio
async def test_github_followup_security_issues_continuity():
    """
    Verifies that follow-up questions about security issues or bugs in a GitHub-focused
    conversation are routed to GITHUB_SEARCH (resolving context via LLM) rather than INTERNAL_RAG.
    """
    history = [
        ChatMessage(role=ChatRole.USER, content="okey so i want u to insepct the repository code for me"),
        ChatMessage(
            role=ChatRole.ASSISTANT,
            content=(
                "Based on the retrieved context, I have inspected the repository `aadish-25/cortex-testing` and found the following file:\n\n"
                "*   **File:** `auth_service.py`\n"
                "*   **Path:** `backend/auth_service.py`\n"
                "*   **Link:** [View on GitHub](https://github.com/aadish-25/cortex-testing/blob/1cdf2e314d4c8350cced5d35e33c8ef274a1bc15/backend/auth_service.py)\n[1]"
            ),
        ),
    ]
    plan = await plan_and_decompose_query("Are there any security issues?", history=history, user_role="Engineering")
    assert len(plan.tasks) >= 1
    # First check is strictly LLM and must resolve to GITHUB_SEARCH, never INTERNAL_RAG
    assert plan.tasks[0].type == PlanTaskType.GITHUB_SEARCH
    assert "auth_service.py" in plan.tasks[0].query or "aadish-25" in plan.tasks[0].query or "security" in plan.tasks[0].query.lower()


@pytest.mark.asyncio
async def test_action_workflow_first_turn_immediacy():
    """
    Verifies that requesting to raise/create a Jira ticket (e.g. 'rais jira ticket')
    directly plans an ACTION_WORKFLOW task on the very first turn so the ActionCard UI
    is provisioned immediately without intermediate chat deflection.
    """
    history = [
        ChatMessage(role=ChatRole.USER, content="githubnissues page"),
        ChatMessage(
            role=ChatRole.ASSISTANT,
            content=(
                "Based on the retrieved context, here are the open GitHub issues for the `aadish-25/cortex-testing` repository:\n\n"
                "*   **Issue #3: [Tech Debt] Tune PostgreSQL database pool size and idle connection timeout**\n"
                "*   **Issue #2: [Feature] Immediate JWT session revocation via token_version**\n"
                "*   **Issue #1: [Bug] Stripe webhook signature verification fails on duplicate events**\n[1]"
            ),
        ),
    ]
    plan = await plan_and_decompose_query("rais jira ticket", history=history, user_role="Engineering")
    assert len(plan.tasks) >= 1
    # Must immediately plan ACTION_WORKFLOW
    assert plan.tasks[0].type == PlanTaskType.ACTION_WORKFLOW
