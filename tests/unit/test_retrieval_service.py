import pytest
from unittest.mock import MagicMock
from qdrant_client import models

from backend.models.enums import ClearanceLevel, Role
from backend.models.user import User
from backend.services.retrieval.reranker_service import (
    ConfidenceLevel,
    classify_confidence,
    rerank_chunks,
)
from backend.services.retrieval.retrieval_service import (
    FALLBACK_STRING,
    build_3d_rbac_filter,
    reciprocal_rank_fusion,
    internal_knowledge_search,
)
from backend.db.qdrant import get_qdrant_client


def test_classify_confidence_tiers():
    """Verifies deterministic confidence boundary mapping (Decisions D10 & D11)."""
    assert classify_confidence(0.95) == ConfidenceLevel.HIGH
    assert classify_confidence(0.35) == ConfidenceLevel.HIGH
    assert classify_confidence(0.349) == ConfidenceLevel.FUZZY
    assert classify_confidence(0.15) == ConfidenceLevel.FUZZY
    assert classify_confidence(0.149) == ConfidenceLevel.LOW
    assert classify_confidence(-0.5) == ConfidenceLevel.LOW


def test_build_3d_rbac_filter_admin():
    """Admin filter is unscoped by department/clearance; requires only is_live=True."""
    admin = User(
        username="admin",
        email="admin@cortex.local",
        hashed_password="x",
        role=Role.ADMIN,
        department="Engineering",
        clearance_level=ClearanceLevel.CONFIDENTIAL,
    )
    q_filter = build_3d_rbac_filter(admin)
    assert len(q_filter.must) == 1
    assert q_filter.must[0].key == "is_live"
    assert q_filter.must[0].match.value is True
    assert q_filter.should is None


def test_build_3d_rbac_filter_employee():
    """Employee filter must enforce clearance ceiling and department match or Public."""
    employee = User(
        username="emp_eng",
        email="emp.eng@cortex.local",
        hashed_password="x",
        role=Role.EMPLOYEE,
        department="Engineering",
        clearance_level=ClearanceLevel.INTERNAL,
    )
    q_filter = build_3d_rbac_filter(employee)
    assert len(q_filter.must) == 2
    # Check is_live condition
    assert q_filter.must[0].key == "is_live"
    # Check clearance condition (Internal can only see Public and Internal)
    assert q_filter.must[1].key == "clearance_level"
    assert "Internal" in q_filter.must[1].match.any
    assert "Public" in q_filter.must[1].match.any
    assert "Confidential" not in q_filter.must[1].match.any
    assert "Restricted" not in q_filter.must[1].match.any

    # Check should (department match or Public)
    assert len(q_filter.should) == 2
    dept_cond = [c for c in q_filter.should if c.key == "department"][0]
    assert dept_cond.match.value == "Engineering"


def test_reciprocal_rank_fusion_math():
    """Verifies RRF formula RRF(d) = sum(1 / (60 + rank)) and deduplication."""
    hit_a = MagicMock()
    hit_a.id = "doc_A"
    hit_a.payload = {"title": "Doc A", "text": "Content A"}

    hit_b = MagicMock()
    hit_b.id = "doc_B"
    hit_b.payload = {"title": "Doc B", "text": "Content B"}

    hit_c = MagicMock()
    hit_c.id = "doc_C"
    hit_c.payload = {"title": "Doc C", "text": "Content C"}

    # Dense ranking: [Doc A (rank 0), Doc B (rank 1)]
    dense_hits = [hit_a, hit_b]
    # Sparse ranking: [Doc B (rank 0), Doc C (rank 1)]
    sparse_hits = [hit_b, hit_c]

    # Expected scores:
    # Doc A: 1/(60 + 0 + 1) = 1/61 = 0.016393
    # Doc B: 1/61 (from sparse rank 0) + 1/62 (from dense rank 1) = 0.016393 + 0.016129 = 0.032522
    # Doc C: 1/(60 + 1 + 1) = 1/62 = 0.016129
    # Winner must be Doc B (present in both lists)
    results = reciprocal_rank_fusion(dense_hits, sparse_hits, k=60, top_k=3)
    assert len(results) == 3
    assert results[0]["id"] == "doc_B"
    assert results[1]["id"] == "doc_A"
    assert results[2]["id"] == "doc_C"


def test_rerank_chunks_neural_ordering():
    """Verifies cross-encoder properly scores relevant passage higher than irrelevant passage."""
    query = "What is the per diem meal allowance for travel?"
    candidates = [
        {
            "id": "c1",
            "text": "The company was founded in 2020 and has 500 employees.",
            "citation": "About Us",
            "section": "History",
            "department": "General",
            "clearance_level": "Public",
        },
        {
            "id": "c2",
            "text": "Daily meal per diem allowance is ₹2,500 per day for traveling staff.",
            "citation": "Travel Policy",
            "section": "Per Diem",
            "department": "Engineering",
            "clearance_level": "Internal",
        },
    ]

    reranked = rerank_chunks(query, candidates, top_k=2)
    assert len(reranked) == 2
    # Candidate 2 (c2) directly answers the question, must rank #1
    assert reranked[0].chunk_id == "c2"
    assert reranked[0].score > reranked[1].score


@pytest.mark.asyncio
async def test_internal_knowledge_search_3d_rbac_enforcement():
    """
    Live Integration Test against Qdrant container:
    Verifies that an Engineering Employee searching for confidential HR content
    receives 0 results (pruned at pre-filter level, zero leakage).
    """
    client = get_qdrant_client()
    eng_employee = User(
        username="emp_eng",
        email="emp.eng@cortex.local",
        hashed_password="x",
        role=Role.EMPLOYEE,
        department="Engineering",
        clearance_level=ClearanceLevel.INTERNAL,
    )

    # POSH Act is HR Department with Confidential clearance
    result = await internal_knowledge_search(
        client=client,
        user=eng_employee,
        query="Internal Complaints Committee POSH Act 2013 procedure",
        top_k=5,
    )

    # Must be blocked by 3D RBAC pre-filter
    assert result.confidence == ConfidenceLevel.LOW
    assert len(result.chunks) == 0
    assert result.fallback_message == FALLBACK_STRING


@pytest.mark.asyncio
async def test_internal_knowledge_search_high_confidence_match():
    """
    Live Integration Test against Qdrant container:
    Verifies that an Engineering Employee searching for engineering travel
    retrieves the exact policy passage with HIGH confidence.
    """
    client = get_qdrant_client()
    eng_employee = User(
        username="emp_eng",
        email="emp.eng@cortex.local",
        hashed_password="x",
        role=Role.EMPLOYEE,
        department="Engineering",
        clearance_level=ClearanceLevel.INTERNAL,
    )

    result = await internal_knowledge_search(
        client=client,
        user=eng_employee,
        query="What is the meal allowance for engineering offsite travel?",
        top_k=5,
    )

    assert result.confidence == ConfidenceLevel.HIGH
    assert len(result.chunks) > 0
    # Must retrieve engineering travel chunks
    assert any("2,500" in c.text or "per diem" in c.text.lower() for c in result.chunks)
