import logging
import re
from typing import Optional

from qdrant_client import QdrantClient
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.config import settings
from backend.models.user import User
from backend.services.retrieval.reranker_service import ConfidenceLevel, ScoredChunk, classify_confidence, rerank_chunks
from backend.services.retrieval.retrieval_service import (
    FALLBACK_STRING,
    SearchResult,
    build_3d_rbac_filter,
    execute_hybrid_search,
    reciprocal_rank_fusion,
)
from backend.services.retrieval.updating_policy_probe import probe_updating_policies

logger = logging.getLogger(__name__)

# Stopwords and conversational fluff to strip for CRAG query reformulation
CONVERSATIONAL_FLUFF_PATTERN = re.compile(
    r"\b(could\s+you|can\s+you|please|tell\s+me|i\s+want\s+to\s+know|what\s+is|how\s+to|explain|briefly|details\s+about)\b",
    re.IGNORECASE,
)


def reformulate_query_for_crag(query: str) -> str:
    """
    Extracts core noun phrases and removes conversational fluff (PRD 6.3).
    Example: 'Can you please explain the meal allowance rules?' -> 'meal allowance rules'
    """
    cleaned = CONVERSATIONAL_FLUFF_PATTERN.sub("", query)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned if len(cleaned) > 3 else query


async def execute_crag_recovery(
    client: QdrantClient,
    session: AsyncSession,
    user: User,
    original_query: str,
    top_k: int = 5,
) -> SearchResult:
    """
    Single-Pass Corrective RAG (CRAG) Engine (PRD 6.3):
    1. Condition: Called when top reranker score is in fuzzy zone (0.15 <= score < 0.35).
    2. Strips conversational fluff to generate keyword-focused query.
    3. Runs a second hybrid search + RRF + Cross-Encoder pass.
    4. If new score >= 0.35, upgrades to HIGH confidence.
    5. If still < 0.35, routes to Decision D9 Updating Policy Probe or safe fallback.
    6. Strictly single-pass (no loops).
    """
    logger.info("Executing single-pass CRAG recovery for a fuzzy query")

    # 1. Reformulate query
    crag_query = reformulate_query_for_crag(original_query)

    # 2. Secondary hybrid retrieval pass
    rbac_filter = build_3d_rbac_filter(user)
    dense_hits, sparse_hits = await execute_hybrid_search(client, crag_query, rbac_filter)
    candidates = reciprocal_rank_fusion(dense_hits, sparse_hits, top_k=25)

    if candidates:
        # 3. Cross-encoder re-evaluation
        reranked = rerank_chunks(crag_query, candidates, top_k=top_k)
        top_score = reranked[0].score if reranked else 0.0
        confidence = classify_confidence(top_score)

        if confidence == ConfidenceLevel.HIGH:
            logger.info("CRAG recovery succeeded! Score upgraded from fuzzy to %.3f (HIGH)", top_score)
            return SearchResult(
                query=original_query,
                confidence=ConfidenceLevel.HIGH,
                chunks=reranked,
                fallback_message=None,
            )

    # 4. If CRAG pass fails to reach HIGH confidence, run Decision D9 Updating Policy Probe
    updating_notice = await probe_updating_policies(
        client=client,
        session=session,
        user=user,
        query=original_query,
    )
    if updating_notice:
        return SearchResult(
            query=original_query,
            confidence=ConfidenceLevel.LOW,
            chunks=[],
            fallback_message=updating_notice,
        )

    # 5. Safe uniform fallback
    return SearchResult(
        query=original_query,
        confidence=ConfidenceLevel.LOW,
        chunks=[],
        fallback_message=FALLBACK_STRING,
    )
