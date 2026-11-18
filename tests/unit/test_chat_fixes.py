"""
Unit Tests for Chat Pipeline Architectural Fixes (PRD Audit Fixes F1-F9)

Tests:
  - F1: Conversation history injected into build_sandboxed_prompt.
  - F2: Web search failure returns empty list without fabricating fake domains.
  - F3: External web search isolation in synthesis prompt.
  - F4: Citations bound to individual chunks via numeric anchor map.
  - F6: Stream interrupted payload emitted on partial generation failure.
  - F8: Evidence chunks retain chunk_id, section, and department.
  - PRD 6.2 / D16: Pronoun-triggered rewriting strictly bypasses standalone queries.
"""

from unittest.mock import AsyncMock, MagicMock, patch
import uuid
import pytest

from backend.models.chat import ChatMessage, ChatRole, ExecutionPlan, PlanTask, PlanTaskType
from backend.models.enums import ClearanceLevel
from backend.services.orchestration.chat_service import build_sandboxed_prompt
from backend.services.orchestration.dag_orchestrator import synthesis_join_node
from backend.services.orchestration.query_planner import rewrite_query
from backend.services.orchestration.web_search_service import execute_web_search
from backend.services.retrieval.reranker_service import ScoredChunk
from backend.services.retrieval.retrieval_service import SearchResult, ConfidenceLevel


def test_build_sandboxed_prompt_injects_conversation_history():
    """F1 Test: Confirms conversation history is injected into user prompt for synthesis."""
    history = [
        ChatMessage(role=ChatRole.USER, content="What is the travel policy?"),
        ChatMessage(role=ChatRole.ASSISTANT, content="The travel policy allows ₹2,500 daily allowance."),
    ]
    system_prompt, user_prompt = build_sandboxed_prompt(
        user_message="What about flights?",
        raw_evidence="Flight bookings must be made 7 days in advance.",
        archival_memories=[],
        conversation_history=history,
    )

    assert "<conversation_history>" in user_prompt
    assert "What is the travel policy?" in user_prompt
    assert "₹2,500" in user_prompt
    assert "<retrieved_context>" in user_prompt
    assert "Flight bookings must be made" in user_prompt


def test_build_sandboxed_prompt_system_rules():
    """F3 & F4 Test: Confirms prompt sandboxing, external web rules, and inline citations."""
    system_prompt, user_prompt = build_sandboxed_prompt(
        user_message="Summarize policy",
        raw_evidence="Evidence here",
        archival_memories=["Prefers bullet points"],
    )

    assert "INLINE CITATIONS" in system_prompt
    assert "EXTERNAL WEB SOURCES" in system_prompt
    assert "<user_preferences_and_context>" in user_prompt
    assert "Prefers bullet points" in user_prompt


@pytest.mark.asyncio
async def test_web_search_failure_returns_empty_never_fabricates():
    """F2 Test: When search network fails, returns empty list — NEVER fabricates fake domains."""
    with patch("httpx.AsyncClient.get", side_effect=Exception("Network unreachable")):
        results = await execute_web_search("Engineering salary benchmark")
        assert results == []
        assert not any("verified-external-source.org" in r.url for r in results)


def test_synthesis_join_node_numeric_citation_map_and_evidence():
    """F4 & F8 Test: Verifies synthesis_join_node outputs numbered citation map and evidence chunks."""
    plan = ExecutionPlan(
        tasks=[
            PlanTask(id=1, type=PlanTaskType.INTERNAL_RAG, query="Leave policy"),
        ],
        original_query="Leave policy",
    )
    chunk1 = ScoredChunk(
        chunk_id="chk_uuid_001",
        text="Sick leave entitlement is 12 days per calendar year.",
        citation="HR Policy v2, Section: Sick Leave",
        section="Sick Leave",
        department="HR",
        clearance_level="Internal",
        score=0.88,
        payload={"doc_id": "doc_001", "chunk_index": 3},
    )
    chunk2 = ScoredChunk(
        chunk_id="chk_uuid_002",
        text="Casual leave entitlement is 8 days per year.",
        citation="HR Policy v2, Section: Casual Leave",
        section="Casual Leave",
        department="HR",
        clearance_level="Internal",
        score=0.75,
        payload={"doc_id": "doc_001", "chunk_index": 4},
    )
    search_result = SearchResult(
        query="Leave policy",
        confidence=ConfidenceLevel.HIGH,
        chunks=[chunk1, chunk2],
    )

    response_text, citation_map, evidence_chunks = synthesis_join_node(
        plan=plan,
        task_outputs={1: search_result},
        all_citations=[chunk1.citation, chunk2.citation],
    )

    # F4: Numeric citation map
    assert 1 in citation_map
    assert 2 in citation_map
    assert citation_map[1] == "HR Policy v2, Section: Sick Leave"
    assert citation_map[2] == "HR Policy v2, Section: Casual Leave"

    # F8: Evidence chunks have chunk_id for 3-chunk sandwich lookup
    assert len(evidence_chunks) == 2
    assert evidence_chunks[0]["chunk_id"] == "chk_uuid_001"
    assert evidence_chunks[0]["anchor"] == 1
    assert evidence_chunks[1]["chunk_id"] == "chk_uuid_002"
    assert evidence_chunks[1]["anchor"] == 2

    # Formatted response text contains numeric anchors
    assert "[1] [HR Policy v2, Section: Sick Leave]" in response_text
    assert "[2] [HR Policy v2, Section: Casual Leave]" in response_text


@pytest.mark.asyncio
async def test_rewrite_query_bypasses_standalone_queries():
    """PRD 6.2 / Decision D16: Confirms standalone follow-up queries with no pronouns bypass rewrite."""
    history = [
        ChatMessage(role=ChatRole.USER, content="How many sick leaves do I have?"),
        ChatMessage(role=ChatRole.ASSISTANT, content="You have 10 days of sick leave remaining."),
    ]

    # Standalone query: No pronouns or anaphoric references
    query = "What is the policy for leave encashment in Engineering?"
    resolved_query, was_rewritten = await rewrite_query(query=query, history=history)

    assert was_rewritten is False
    assert resolved_query == query


@pytest.mark.asyncio
async def test_rewrite_query_triggers_on_pronoun_reference():
    """PRD 6.2 / Decision D16: Confirms query with pronoun 'it' triggers rewrite within last 2 turns."""
    history = [
        ChatMessage(role=ChatRole.USER, content="Tell me about the Engineering travel allowance."),
        ChatMessage(role=ChatRole.ASSISTANT, content="The allowance is ₹2,500 per day."),
    ]

    query = "Can I claim it for personal trips?"
    with patch("backend.services.orchestration.llm_service.llm_service.is_configured", return_value=False):
        # Fallback heuristic path
        resolved_query, was_rewritten = await rewrite_query(query=query, history=history)
        assert was_rewritten is True
        assert "Engineering travel allowance" in resolved_query


def test_archival_memory_lookup_only_for_personal_context_prompts():
    from backend.services.orchestration.chat_service import should_search_archival_memory

    assert should_search_archival_memory("What is my leave balance?") is True
    assert should_search_archival_memory("Can you use what you remember about me?") is True
    assert should_search_archival_memory("What is the annual leave policy?") is False
    assert should_search_archival_memory("Explain the security policy") is False


def test_evidence_tokens_are_user_bound_and_opaque():
    from backend.core.evidence_tokens import create_evidence_token, read_evidence_token

    owner_id = uuid.uuid4()
    other_id = uuid.uuid4()
    token = create_evidence_token("qdrant-point-uuid", owner_id)
    assert "qdrant-point-uuid" not in token
    assert read_evidence_token(token, owner_id) == "qdrant-point-uuid"
    with pytest.raises(ValueError):
        read_evidence_token(token, other_id)
