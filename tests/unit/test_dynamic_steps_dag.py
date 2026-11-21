import pytest
from backend.models.chat import ExecutionPlan, PlanTask, PlanTaskType, ToolAction
from backend.services.orchestration.dag_orchestrator import (
    SYSTEM_DAG_EDGES,
    SYSTEM_DAG_NODES,
    build_dag_trace,
)
from backend.services.retrieval.reranker_service import ConfidenceLevel


def test_build_dag_trace_structure():
    """Verifies that build_dag_trace outputs all system nodes, correctly flags active branches, and skips others."""
    plan = ExecutionPlan(
        tasks=[
            PlanTask(
                id=1,
                type=PlanTaskType.GITHUB_SEARCH,
                tool_action=ToolAction.GITHUB_COMMIT_HISTORY,
                query="",
            ),
        ]
    )
    task_outputs = {
        1: {
            "status": "SUCCESS",
            "commits": [{"sha": "4cddfac", "message": "fix: fk", "author": "aadishpcs"}],
        }
    }
    task_latencies = {1: 85.5}

    trace = build_dag_trace(
        query="show me the recent commit history",
        plan=plan,
        task_outputs=task_outputs,
        task_latencies=task_latencies,
        overall_confidence=ConfidenceLevel.HIGH,
    )

    assert "nodes" in trace
    assert "edges" in trace
    assert "executed_node_ids" in trace
    assert "node_details" in trace
    assert "total_latency_ms" in trace

    # Total nodes should match full architecture suite (14 nodes)
    assert len(trace["nodes"]) == len(SYSTEM_DAG_NODES)

    # Executed nodes should include core pipeline + github_search
    executed = trace["executed_node_ids"]
    assert "user_query" in executed
    assert "memory_context" in executed
    assert "planner" in executed
    assert "github_search" in executed
    assert "synthesis" in executed

    # Unexecuted nodes (e.g. drive_search, web_search, internal_rag) must be marked skipped
    assert "drive_search" not in executed
    assert "web_search" not in executed
    assert trace["node_details"]["drive_search"]["status"] == "skipped"
    assert trace["node_details"]["drive_search"]["latency_ms"] == 0.0

    # Active edges must connect executed nodes only
    for edge in trace["edges"]:
        if edge["active"]:
            assert edge["source"] in executed
            assert edge["target"] in executed


def test_build_dag_trace_internal_rag_and_crag():
    """Verifies that internal_rag triggers reranker and crag_recovery on fuzzy confidence."""
    from backend.services.retrieval.retrieval_service import SearchResult
    from backend.services.retrieval.reranker_service import ScoredChunk

    plan = ExecutionPlan(
        tasks=[
            PlanTask(id=1, type=PlanTaskType.INTERNAL_RAG, query="leave policy"),
        ]
    )
    fake_chunk = ScoredChunk(
        chunk_id="chunk-123",
        document_id="doc-456",
        score=0.42,
        text="Annual leave is 20 days",
        citation="Leave Policy v1",
        section="Annual Leave",
        department="HR",
        clearance_level="Internal",
    )
    task_outputs = {
        1: SearchResult(
            chunks=[fake_chunk],
            confidence=ConfidenceLevel.FUZZY,
            top_score=0.42,
        )
    }
    task_latencies = {1: 120.0}

    trace = build_dag_trace(
        query="leave policy",
        plan=plan,
        task_outputs=task_outputs,
        task_latencies=task_latencies,
        overall_confidence=ConfidenceLevel.FUZZY,
    )

    executed = trace["executed_node_ids"]
    assert "internal_rag" in executed
    assert "reranker" in executed
    assert "crag_recovery" in executed
    assert trace["node_details"]["crag_recovery"]["status"] == "completed"
