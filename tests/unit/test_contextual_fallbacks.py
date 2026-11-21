import pytest
from backend.models.chat import ExecutionPlan, PlanTask, PlanTaskType
from backend.services.orchestration.chat_service import (
    UNIFORM_FALLBACK_STRING,
    resolve_contextual_fallback,
)
from backend.services.orchestration.dag_orchestrator import OrchestratorResponse
from backend.services.retrieval.reranker_service import ConfidenceLevel


def test_fallback_disambiguation():
    """Verifies that an ambiguous query requiring clarification returns the polite disambiguation prompt."""
    plan = ExecutionPlan(
        tasks=[
            PlanTask(
                id=1,
                type=PlanTaskType.DIRECT_CHAT,
                query="I couldn't get your message, do you mean GitHub issues or do you mean something else?",
            )
        ]
    )
    orch_res = OrchestratorResponse(plan=plan, final_response="", confidence=ConfidenceLevel.LOW)
    msg = resolve_contextual_fallback(orch_res, "what are the bugs")
    assert "do you mean GitHub issues" in msg


def test_fallback_github_search():
    """Verifies that a GitHub search with zero results returns repository-specific notice."""
    plan = ExecutionPlan(
        tasks=[
            PlanTask(
                id=1,
                type=PlanTaskType.GITHUB_SEARCH,
                query="nonexistent_function",
            )
        ]
    )
    orch_res = OrchestratorResponse(plan=plan, final_response="", confidence=ConfidenceLevel.LOW)
    msg = resolve_contextual_fallback(orch_res, "search code for nonexistent_function")
    assert "searched the connected repository" in msg
    assert "couldn't find matching code" in msg


def test_fallback_drive_search():
    """Verifies that a Google Drive miss returns drive-specific notice."""
    plan = ExecutionPlan(
        tasks=[
            PlanTask(
                id=1,
                type=PlanTaskType.DRIVE_SEARCH,
                query="missing_presentation.pptx",
            )
        ]
    )
    orch_res = OrchestratorResponse(plan=plan, final_response="", confidence=ConfidenceLevel.LOW)
    msg = resolve_contextual_fallback(orch_res, "find missing_presentation.pptx in drive")
    assert "connected Google Drive" in msg


def test_fallback_sql_lookup():
    """Verifies that an empty SQL lookup returns operational records notice."""
    plan = ExecutionPlan(
        tasks=[
            PlanTask(
                id=1,
                type=PlanTaskType.STRUCTURED_LOOKUP,
                query="select * from tickets where id = 99999",
            )
        ]
    )
    orch_res = OrchestratorResponse(plan=plan, final_response="", confidence=ConfidenceLevel.LOW)
    msg = resolve_contextual_fallback(orch_res, "my tickets")
    assert "operational records" in msg


def test_fallback_internal_rag():
    """Verifies that low confidence internal document search preserves UNIFORM_FALLBACK_STRING."""
    plan = ExecutionPlan(
        tasks=[
            PlanTask(
                id=1,
                type=PlanTaskType.INTERNAL_RAG,
                query="unknown quantum computing policy",
            )
        ]
    )
    orch_res = OrchestratorResponse(plan=plan, final_response="", confidence=ConfidenceLevel.LOW)
    msg = resolve_contextual_fallback(orch_res, "unknown quantum computing policy")
    assert msg == UNIFORM_FALLBACK_STRING
