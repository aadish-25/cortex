import pytest
from unittest.mock import AsyncMock, patch

from backend.models.chat import ExecutionPlan, PlanTask, PlanTaskType, ToolAction
from backend.services.integrations.github_service import github_service
from backend.services.orchestration.dag_orchestrator import execute_task, synthesis_join_node
from backend.models.user import User
from backend.models.enums import ClearanceLevel, Role


@pytest.fixture
def mock_user():
    return User(
        name="Test User",
        username="test_user",
        email="aadishpcs@gmail.com",
        role=Role.EMPLOYEE,
        department="Engineering",
        clearance_level=ClearanceLevel.INTERNAL,
    )


@pytest.mark.asyncio
async def test_get_commit_history_unconfigured():
    """Verifies that get_commit_history returns structured simulated history when PAT is unconfigured."""
    with patch.object(github_service, "is_configured", False):
        res = await github_service.get_commit_history(limit=5)
        assert res["status"] == "SUCCESS"
        assert len(res["commits"]) >= 1
        assert "sha" in res["commits"][0]
        assert "author" in res["commits"][0]
        assert "message" in res["commits"][0]


@pytest.mark.asyncio
async def test_get_issue_comments_unconfigured():
    """Verifies that get_issue_comments returns structured comments when unconfigured."""
    with patch.object(github_service, "is_configured", False):
        res = await github_service.get_issue_comments(issue_number=101)
        assert res["status"] == "SUCCESS"
        assert res["issue_number"] == 101
        assert len(res["comments"]) >= 1
        assert res["comments"][0]["author"] == "aadishpcs"


@pytest.mark.asyncio
async def test_get_pr_diff_unconfigured():
    """Verifies that get_pr_diff returns diff representation."""
    with patch.object(github_service, "is_configured", False):
        res = await github_service.get_pr_diff(pr_number=55)
        assert res["status"] == "SUCCESS"
        assert res["pr_number"] == 55
        assert "diff" in res
        assert "--- a/backend" in res["diff"]


@pytest.mark.asyncio
async def test_get_workflow_status_unconfigured():
    """Verifies that get_workflow_status returns CI/CD workflow runs."""
    with patch.object(github_service, "is_configured", False):
        res = await github_service.get_workflow_status()
        assert res["status"] == "SUCCESS"
        assert len(res["workflows"]) >= 1
        assert res["workflows"][0]["conclusion"] == "success"


@pytest.mark.asyncio
async def test_dag_orchestrator_github_dispatching(mock_user):
    """Verifies that dag_orchestrator routes extended GitHub tool actions properly."""
    task = PlanTask(
        id=1,
        type=PlanTaskType.GITHUB_SEARCH,
        tool_action=ToolAction.GITHUB_COMMIT_HISTORY,
        query="",
    )
    mock_session = AsyncMock()
    mock_client = AsyncMock()

    with patch.object(github_service, "is_configured", False):
        out, cites = await execute_task(
            task=task,
            client=mock_client,
            session=mock_session,
            user=mock_user,
            scoped_upstream_context={},
        )
        assert out["status"] == "SUCCESS"
        assert len(out["commits"]) > 0
        assert len(cites) > 0
        assert "commit" in cites[0]


def test_synthesis_join_node_formatting():
    """Verifies synthesis_join_node renders markdown sections for commit history, PR diff, and workflows."""
    plan = ExecutionPlan(
        tasks=[
            PlanTask(id=1, type=PlanTaskType.GITHUB_SEARCH, tool_action=ToolAction.GITHUB_COMMIT_HISTORY, query=""),
            PlanTask(id=2, type=PlanTaskType.GITHUB_SEARCH, tool_action=ToolAction.GITHUB_WORKFLOW_STATUS, query=""),
        ]
    )
    task_outputs = {
        1: {
            "status": "SUCCESS",
            "repository": "cortex-org/cortex",
            "commits": [{"sha": "abc1234", "author": "aadishpcs", "message": "feat: init", "date": "2026-11-21"}],
        },
        2: {
            "status": "SUCCESS",
            "repository": "cortex-org/cortex",
            "workflows": [{"name": "CI Tests", "status": "completed", "conclusion": "success", "updated_at": "2026-11-21"}],
        },
    }
    final_text, citations, evidence = synthesis_join_node(plan, task_outputs, [])
    assert "GitHub Commit History" in final_text
    assert "abc1234" in final_text
    assert "GitHub Actions CI/CD Workflows" in final_text
    assert "CI Tests" in final_text
