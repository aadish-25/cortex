import asyncio
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from qdrant_client import QdrantClient, models

from backend.core.config import settings
from backend.models.enums import CLEARANCE_RANK, ClearanceLevel, Role
from backend.models.user import User
from backend.services.reranker_service import (
    ConfidenceLevel,
    ScoredChunk,
    classify_confidence,
    rerank_chunks,
)
from backend.services.vector_service import (
    generate_dense_embeddings,
    generate_sparse_embeddings,
)

FALLBACK_STRING = (
    "I couldn't find verified information in the documents you have access to."
)


@dataclass
class SearchResult:
    query: str
    confidence: ConfidenceLevel
    chunks: List[ScoredChunk]
    fallback_message: Optional[str] = None


def build_3d_rbac_filter(user: User) -> models.Filter:
    """
    Constructs Qdrant boolean filter ensuring unauthorized chunks are mathematically unreachable (D3).
    1. is_live == True (excludes staged drafts and old purged versions)
    2. Clearance: clearance_level <= user.clearance_level
    3. Department: department == user.department OR clearance_level == Public
    """
    # System Administrators can search across all live documents enterprise-wide
    if user.role == Role.ADMIN:
        return models.Filter(
            must=[
                models.FieldCondition(
                    key="is_live",
                    match=models.MatchValue(value=True),
                )
            ]
        )

    # Compute allowed clearance levels based on user clearance rank hierarchy
    allowed_clearances = [
        lvl.value
        for lvl, rank in CLEARANCE_RANK.items()
        if rank <= CLEARANCE_RANK[user.clearance_level]
    ]

    return models.Filter(
        must=[
            models.FieldCondition(
                key="is_live",
                match=models.MatchValue(value=True),
            ),
            models.FieldCondition(
                key="clearance_level",
                match=models.MatchAny(any=allowed_clearances),
            ),
        ],
        should=[
            models.FieldCondition(
                key="department",
                match=models.MatchValue(value=user.department),
            ),
            models.FieldCondition(
                key="clearance_level",
                match=models.MatchValue(value=ClearanceLevel.PUBLIC.value),
            ),
        ],
    )


async def execute_hybrid_search(
    client: QdrantClient,
    query: str,
    rbac_filter: models.Filter,
    dense_limit: int = 25,
    sparse_limit: int = 25,
) -> Tuple[List[Any], List[Any]]:
    """
    Generates query vectors and executes Dense + Sparse BM25 searches in parallel via asyncio.gather.
    Uses modern Qdrant client.query_points with named vector targets ('dense' and 'bm25').
    """
    # 1. Embed query into Dense 384d float vector and Sparse BM25 token vector
    query_dense = generate_dense_embeddings([query])[0]
    query_sparse = generate_sparse_embeddings([query])[0]

    loop = asyncio.get_running_loop()

    # 2. Dispatch Dense semantic search in thread pool
    dense_task = loop.run_in_executor(
        None,
        lambda: client.query_points(
            collection_name=settings.QDRANT_COLLECTION,
            query=query_dense,
            using="dense",
            query_filter=rbac_filter,
            limit=dense_limit,
        ).points,
    )

    # 3. Dispatch Sparse BM25 lexical search in thread pool
    sparse_task = loop.run_in_executor(
        None,
        lambda: client.query_points(
            collection_name=settings.QDRANT_COLLECTION,
            query=query_sparse,
            using="bm25",
            query_filter=rbac_filter,
            limit=sparse_limit,
        ).points,
    )

    dense_hits, sparse_hits = await asyncio.gather(dense_task, sparse_task)
    return dense_hits, sparse_hits


def reciprocal_rank_fusion(
    dense_hits: List[Any],
    sparse_hits: List[Any],
    k: int = 60,
    top_k: int = 25,
) -> List[Dict[str, Any]]:
    """
    Fuses dense and sparse result lists using Reciprocal Rank Fusion (RRF).
    Formula: RRF(d) = sum(1 / (k + rank_m(d))) for m in {dense, sparse}.
    Deduplicates chunks and returns top_k candidate payloads.
    """
    rrf_scores: Dict[str, float] = {}
    hit_data: Dict[str, Dict[str, Any]] = {}

    # Accumulate reciprocal rank scores from dense search
    for rank, hit in enumerate(dense_hits):
        doc_id = str(hit.id)
        payload = dict(hit.payload or {})
        payload["id"] = doc_id
        hit_data[doc_id] = payload
        rrf_scores[doc_id] = rrf_scores.get(doc_id, 0.0) + (1.0 / (k + rank + 1))

    # Accumulate reciprocal rank scores from sparse BM25 search
    for rank, hit in enumerate(sparse_hits):
        doc_id = str(hit.id)
        if doc_id not in hit_data:
            payload = dict(hit.payload or {})
            payload["id"] = doc_id
            hit_data[doc_id] = payload
        rrf_scores[doc_id] = rrf_scores.get(doc_id, 0.0) + (1.0 / (k + rank + 1))

    # Sort candidates descending by fused RRF score
    sorted_ids = sorted(
        rrf_scores.keys(), key=lambda doc_id: rrf_scores[doc_id], reverse=True
    )
    return [hit_data[doc_id] for doc_id in sorted_ids[:top_k]]


async def internal_knowledge_search(
    client: QdrantClient,
    user: User,
    query: str,
    top_k: int = 5,
) -> SearchResult:
    """
    End-to-End Enterprise Retrieval Pipeline (Phase 2.5):
    1. Zero-Knowledge 3D RBAC Pre-Filter (Decision D3).
    2. Parallel Dense + BM25 Qdrant Search (asyncio.gather).
    3. Reciprocal Rank Fusion (RRF) -> Top 25 candidates.
    4. FastEmbed Cross-Encoder Re-Ranking -> Top 5 passages (Decision D7).
    5. Calibrated Confidence Cutoff (Decisions D10 & D11).
    """
    # 1. Build RBAC pre-filter (unauthorized vectors are unreachable)
    rbac_filter = build_3d_rbac_filter(user)

    # 2. Execute parallel hybrid retrieval
    dense_hits, sparse_hits = await execute_hybrid_search(client, query, rbac_filter)

    # 3. Fuse candidates via Reciprocal Rank Fusion (RRF)
    candidates = reciprocal_rank_fusion(dense_hits, sparse_hits, top_k=25)
    if not candidates:
        return SearchResult(
            query=query,
            confidence=ConfidenceLevel.LOW,
            chunks=[],
            fallback_message=FALLBACK_STRING,
        )

    # 4. Neural Cross-Encoder Re-ranking
    reranked = rerank_chunks(query, candidates, top_k=top_k)
    top_score = reranked[0].score if reranked else 0.0
    confidence = classify_confidence(top_score)

    # 5. Low confidence cutoff (reject with uniform fallback string, 0 tokens to LLM)
    if confidence == ConfidenceLevel.LOW:
        return SearchResult(
            query=query,
            confidence=confidence,
            chunks=[],
            fallback_message=FALLBACK_STRING,
        )

    return SearchResult(
        query=query,
        confidence=confidence,
        chunks=reranked,
        fallback_message=None,
    )
