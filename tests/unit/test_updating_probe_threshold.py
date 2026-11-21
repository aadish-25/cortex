import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from backend.models.enums import ClearanceLevel, Role
from backend.models.user import User
from backend.services.retrieval.updating_policy_probe import probe_updating_policies


@pytest.fixture
def test_user():
    return User(
        name="Test Employee",
        username="test_emp",
        email="aadishpcs@gmail.com",
        role=Role.EMPLOYEE,
        department="HR",
        clearance_level=ClearanceLevel.INTERNAL,
    )


@pytest.mark.asyncio
async def test_probe_updating_policies_enforces_score_threshold(test_user):
    """Verifies that probe_updating_policies invokes Qdrant with score_threshold=0.30."""
    mock_client = MagicMock()
    mock_session = AsyncMock()

    # Configure query_points mock
    mock_search_res = MagicMock()
    mock_search_res.points = []
    mock_client.query_points.return_value = mock_search_res

    with patch("backend.services.retrieval.updating_policy_probe.generate_dense_embeddings", return_value=[[0.1] * 384]):
        notice = await probe_updating_policies(
            client=mock_client,
            session=mock_session,
            user=test_user,
            query="parental leave policy revision",
            top_k=3,
        )
        assert notice is None
        # Verify score_threshold argument
        mock_client.query_points.assert_called_once()
        call_kwargs = mock_client.query_points.call_args.kwargs
        assert call_kwargs.get("score_threshold") == 0.30
        assert call_kwargs.get("limit") == 3
        assert call_kwargs.get("using") == "dense"
