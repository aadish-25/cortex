import logging
import re
from typing import Optional

from pydantic import BaseModel, Field
from qdrant_client import QdrantClient
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.config import settings
from backend.models.user import User
from backend.services.orchestration.llm_service import llm_service
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


class CRAGQueryRewrite(BaseModel):
    search_query: str = Field(
        description="Concise, typo-corrected, search-optimized English keyword phrase for corporate document retrieval"
    )


# Fallback-only regex when LLM is unavailable
CONVERSATIONAL_FLUFF_PATTERN = re.compile(
    r"\b(hey|hello|hi|could\s+you|could\s+u|can\s+you|can\s+u|please|pls|plz|tell\s+me|i\s+want\s+to\s+know|what\s+is|whats|whts|how\s+to|explain|briefly|details\s+about)\b",
    re.IGNORECASE,
)


def reformulate_query_for_crag(query: str) -> str:
    """
    Offline heuristic fallback: removes conversational fluff (PRD 6.3).
    """
    cleaned = CONVERSATIONAL_FLUFF_PATTERN.sub("", query)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned if len(cleaned) > 3 else query


async def reformulate_query_llm_first(query: str) -> str:
    """
    PRIMARY: Uses Fast LLM with Pydantic schema to fix typos, translate Hinglish,
    and extract a crisp corporate search query.
    FALLBACK: Regex fluff stripping when LLM is unconfigured or errors.
    """
    if llm_service.is_configured():
        try:
            rewritten = await llm_service.generate_structured(
                prompt=f"Original user query: {query}",
                response_model=CRAGQueryRewrite,
                system_prompt=(
                    "Rewrite this query into a concise, spell-corrected English search phrase "
                    "(3 to 8 keywords) optimized for retrieving internal corporate policies and technical docs. "
                    "Strip greetings and conversational filler; fix spelling errors and Hinglish."
                ),
                model=settings.FAST_ROUTING_MODEL,
            )
            if rewritten and rewritten.search_query and len(rewritten.search_query.strip()) >= 3:
                return rewritten.search_query.strip()
        except Exception as exc:
            logger.warning("LLM CRAG reformulation failed (%s); falling back to regex", exc)
    return reformulate_query_for_crag(query)


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

    # 1. Reformulate query (LLM-first, regex strictly fallback)
    crag_query = await reformulate_query_llm_first(original_query)

    # 2. Secondary hybrid retrieval pass
    rbac_filter = build_3d_rbac_filter(user)
    dense_hits, sparse_hits = await execute_hybrid_search(client, crag_query, rbac_filter)
    candidates = reciprocal_rank_fusion(dense_hits, sparse_hits, top_k=25)

    top_score_val: Optional[float] = None
    scores_val: list[float] = []

    if candidates:
        # 3. Cross-encoder re-evaluation
        reranked = rerank_chunks(crag_query, candidates, top_k=top_k)
        scores_val = [float(c.score) for c in reranked]
        top_score = scores_val[0] if scores_val else 0.0
        top_score_val = top_score if scores_val else None
        confidence = classify_confidence(top_score)

        if confidence == ConfidenceLevel.HIGH:
            logger.info("CRAG recovery succeeded! Score upgraded to %.3f (HIGH)", top_score)
            return SearchResult(
                query=original_query,
                confidence=ConfidenceLevel.HIGH,
                chunks=reranked,
                fallback_message=None,
                top_score=top_score_val,
                reranker_scores=scores_val,
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
            top_score=top_score_val,
            reranker_scores=scores_val,
        )

    # 5. Safe uniform fallback
    return SearchResult(
        query=original_query,
        confidence=ConfidenceLevel.LOW,
        chunks=[],
        fallback_message=FALLBACK_STRING,
        top_score=top_score_val,
        reranker_scores=scores_val,
    )
