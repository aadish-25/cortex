"""
Unit Tests for Reconciliation Sweeper (Phase 7 — Decision D8)

Tests:
  - Redis distributed lock acquisition and release.
  - Non-blocking skip when lock is already held by another worker.
  - Forward pass: Flags active documents missing vectors in Qdrant as UPDATING.
  - Reverse pass: Purges orphan vectors from Qdrant whose parent document is deleted/inactive.
"""

from unittest.mock import AsyncMock, MagicMock, patch
import uuid
import pytest

from backend.models.document import Document
from backend.models.enums import ClearanceLevel, DocumentStatus
from backend.worker.sweeper import RECONCILE_LOCK_KEY, run_reconciliation_sweeper


@pytest.mark.asyncio
async def test_sweeper_skipped_when_lock_held(async_session_factory):
    """Verifies that sweeper immediately skips if another worker holds the lock."""
    async with async_session_factory() as session:
        mock_redis = AsyncMock()
        # Return False simulating lock already held
        mock_redis.set.return_value = False

        mock_qdrant = MagicMock()

        with patch("backend.worker.sweeper.get_redis_client", return_value=mock_redis):
            res = await run_reconciliation_sweeper(session, mock_qdrant)
            assert res["status"] == "SKIPPED"
            assert "already held" in res["reason"]
            mock_qdrant.count.assert_not_called()
            mock_qdrant.delete.assert_not_called()


@pytest.mark.asyncio
async def test_sweeper_forward_pass_flags_missing_vectors(async_session_factory):
    """
    Verifies that active SQL documents with 0 vectors in Qdrant are flagged
    with status=UPDATING and is_live=False for re-indexing.
    """
    async with async_session_factory() as session:
        mock_redis = AsyncMock()
        mock_redis.set.return_value = True

        doc = Document(
            filename="missing_vectors.md",
            file_hash=f"hash_{uuid.uuid4().hex}",
            title="Missing Vectors Doc",
            department="Engineering",
            clearance_level=ClearanceLevel.INTERNAL,
            status=DocumentStatus.ACTIVE,
            version=1,
            is_live=True,
        )
        session.add(doc)
        await session.commit()
        await session.refresh(doc)

        mock_qdrant = MagicMock()
        # Simulate 0 points found in Qdrant for this document
        count_res = MagicMock()
        count_res.count = 0
        mock_qdrant.count.return_value = count_res
        mock_qdrant.scroll.return_value = ([], None)

        with patch("backend.worker.sweeper.get_redis_client", return_value=mock_redis):
            res = await run_reconciliation_sweeper(session, mock_qdrant)
            assert res["status"] == "SUCCESS"
            assert res["missing_docs_flagged"] >= 1

            # Verify document state in DB was updated
            await session.refresh(doc)
            assert doc.status == DocumentStatus.UPDATING
            assert doc.is_live is False

            # Lock should be deleted in finally block
            mock_redis.delete.assert_called_with(RECONCILE_LOCK_KEY)


@pytest.mark.asyncio
async def test_sweeper_reverse_pass_purges_orphan_vectors(async_session_factory):
    """
    Verifies that Qdrant vectors referencing non-existent or inactive doc_ids
    are purged via qdrant_client.delete.
    """
    async with async_session_factory() as session:
        mock_redis = AsyncMock()
        mock_redis.set.return_value = True

        mock_qdrant = MagicMock()
        count_res = MagicMock()
        count_res.count = 5
        mock_qdrant.count.return_value = count_res

        # Simulate points in Qdrant with an unknown doc_id
        orphan_doc_id = str(uuid.uuid4())
        point1 = MagicMock(id="p1", payload={"doc_id": orphan_doc_id})
        mock_qdrant.scroll.return_value = ([point1], None)

        with patch("backend.worker.sweeper.get_redis_client", return_value=mock_redis):
            res = await run_reconciliation_sweeper(session, mock_qdrant)
            assert res["status"] == "SUCCESS"
            assert res["orphan_points_purged"] >= 1

            # Ensure Qdrant delete was called for orphan point
            mock_qdrant.delete.assert_called_once()
