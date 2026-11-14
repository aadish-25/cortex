import uuid
import pytest
from qdrant_client import QdrantClient, models

from backend.models.enums import ClearanceLevel
from backend.db.qdrant import get_qdrant_client, init_qdrant_collection
from backend.services.chunking_service import DocumentChunk
from backend.services.embedding_service import (
    generate_dense_embeddings,
    generate_sparse_embeddings,
)
from backend.services.vector_service import (
    activate_document_version,
    index_document_chunks,
)


@pytest.fixture
def memory_qdrant():
    """Provides an isolated, in-memory Qdrant client for fast and independent unit testing."""
    client = QdrantClient(":memory:")
    init_qdrant_collection(client, collection_name="test_cortex_collection")
    return client


def test_qdrant_collection_creation(memory_qdrant: QdrantClient):
    """Verifies that the collection has both Dense (384d) and Sparse BM25 vector configs."""
    info = memory_qdrant.get_collection("test_cortex_collection")
    assert info is not None
    # Check dense vector config
    assert "dense" in info.config.params.vectors
    dense_cfg = info.config.params.vectors["dense"]
    assert dense_cfg.size == 384
    assert dense_cfg.distance == models.Distance.COSINE
    # Check sparse vector config
    assert info.config.params.sparse_vectors is not None
    assert "bm25" in info.config.params.sparse_vectors


def test_dense_and_sparse_embedding_generation():
    """Verifies that FastEmbed models generate valid dense and sparse vector structures."""
    texts = ["Engineering Travel Policy in Bengaluru", "POSH Act 2013 Compliance"]
    dense_vecs = generate_dense_embeddings(texts)
    sparse_vecs = generate_sparse_embeddings(texts)

    assert len(dense_vecs) == 2
    assert len(dense_vecs[0]) == 384
    assert len(dense_vecs[1]) == 384

    assert len(sparse_vecs) == 2
    assert len(sparse_vecs[0].indices) > 0
    assert len(sparse_vecs[0].values) == len(sparse_vecs[0].indices)


def test_index_document_chunks_staged_version_flip(memory_qdrant: QdrantClient):
    """
    Verifies that chunks are initially indexed with is_live=False (Decision D4)
    and contain all 3D RBAC payload fields.
    """
    doc_id = uuid.uuid4()
    chunks = [
        DocumentChunk(
            text="Meal allowance is ₹2,500/day in Bengaluru.",
            section="Daily Allowance",
            page_number=None,
            citation="[Travel Policy, Section: Daily Allowance]",
            stamped_content="[Document: Travel Policy | Version: 1 | Department: Engineering | Section: Daily Allowance]\n\nMeal allowance is ₹2,500/day in Bengaluru.",
        )
    ]

    count = index_document_chunks(
        client=memory_qdrant,
        document_id=doc_id,
        version=1,
        department="Engineering",
        clearance_level=ClearanceLevel.INTERNAL,
        chunks=chunks,
        is_live=False,
        collection_name="test_cortex_collection",
    )
    assert count == 1

    # Verify points stored in Qdrant
    res = memory_qdrant.scroll(
        collection_name="test_cortex_collection",
        scroll_filter=models.Filter(
            must=[
                models.FieldCondition(
                    key="document_id",
                    match=models.MatchValue(value=str(doc_id)),
                )
            ]
        ),
        limit=10,
    )
    points, _ = res
    assert len(points) == 1
    p = points[0]
    assert p.payload["is_live"] is False  # Staged flip: starts inactive
    assert p.payload["department"] == "Engineering"
    assert p.payload["clearance_level"] == "Internal"
    assert p.payload["version"] == 1
    assert "₹2,500" in p.payload["text"]


def test_activate_document_version_and_purge_old(memory_qdrant: QdrantClient):
    """
    Verifies Decision D4 staged version flip switchover:
    - Activating Version 2 sets is_live=True for v2.
    - Purges Version 1 chunks so no stale duplicates remain.
    """
    doc_id = uuid.uuid4()

    # Index Version 1 (currently live)
    v1_chunks = [
        DocumentChunk(
            text="Version 1 old content",
            section="Sec 1",
            page_number=1,
            citation="[Doc, p. 1]",
            stamped_content="[Document: Doc | Version: 1 | Department: HR | Section: Sec 1]\n\nVersion 1 old content",
        )
    ]
    index_document_chunks(
        client=memory_qdrant,
        document_id=doc_id,
        version=1,
        department="HR",
        clearance_level=ClearanceLevel.INTERNAL,
        chunks=v1_chunks,
        is_live=True,  # Old version was live
        collection_name="test_cortex_collection",
    )

    # Index Version 2 (staged, is_live=False)
    v2_chunks = [
        DocumentChunk(
            text="Version 2 new updated content",
            section="Sec 1",
            page_number=1,
            citation="[Doc, p. 1]",
            stamped_content="[Document: Doc | Version: 2 | Department: HR | Section: Sec 1]\n\nVersion 2 new updated content",
        )
    ]
    index_document_chunks(
        client=memory_qdrant,
        document_id=doc_id,
        version=2,
        department="HR",
        clearance_level=ClearanceLevel.INTERNAL,
        chunks=v2_chunks,
        is_live=False,  # New version staged
        collection_name="test_cortex_collection",
    )

    # Pre-check: 2 points exist in Qdrant (1 live, 1 staged)
    all_points, _ = memory_qdrant.scroll("test_cortex_collection", limit=10)
    assert len(all_points) == 2

    # Execute atomic version activation (D4)
    activate_document_version(
        client=memory_qdrant,
        document_id=doc_id,
        active_version=2,
        collection_name="test_cortex_collection",
    )

    # Post-check: Only Version 2 remains, and is_live is flipped to True
    remaining_points, _ = memory_qdrant.scroll("test_cortex_collection", limit=10)
    assert len(remaining_points) == 1
    assert remaining_points[0].payload["version"] == 2
    assert remaining_points[0].payload["is_live"] is True
    assert "Version 2 new updated content" in remaining_points[0].payload["text"]
