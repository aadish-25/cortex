from dataclasses import dataclass
from enum import Enum
from typing import List, Dict, Any, Optional
from fastembed.rerank.cross_encoder import TextCrossEncoder
from backend.core.config import settings

_reranker_instance: Optional[TextCrossEncoder] = None


class ConfidenceLevel(str, Enum):
    HIGH = "HIGH"
    FUZZY = "FUZZY"
    LOW = "LOW"


@dataclass
class ScoredChunk:
    chunk_id: str
    text: str
    citation: str
    section: str
    department: str
    clearance_level: str
    score: float
    payload: Dict[str, Any]


def classify_confidence(top_score: float) -> ConfidenceLevel:
    """Classifies top reranker score into deterministic confidence tiers."""
    if top_score >= settings.RERANKER_CONFIDENCE_THRESHOLD:  # 0.35
        return ConfidenceLevel.HIGH
    elif top_score >= 0.15:
        return ConfidenceLevel.FUZZY
    return ConfidenceLevel.LOW


def get_reranker() -> TextCrossEncoder:
    """Lazy-loaded singleton instance of FastEmbed Cross-Encoder."""
    global _reranker_instance

    if _reranker_instance is None:
        _reranker_instance = TextCrossEncoder(
            model_name="Xenova/ms-marco-MiniLM-L-6-v2"
        )
    return _reranker_instance


def rerank_chunks(
    query: str,
    candidates: List[Dict[str, Any]],
    top_k: int = 5,
) -> List[ScoredChunk]:
    """
    Evaluates (query, candidate_text) pairs with deep cross-attention.
    Eliminates bi-encoder semantic drift and returns top_k sorted passages.
    """
    if not candidates:
        return []

    reranker = get_reranker()

    # Prefer stamped_content (D6 Identity Header) if present for contextual grounding
    passages = [c.get("stamped_content") or c.get("text", "") for c in candidates]

    # Generate cross-encoder float scores
    raw_scores = list(reranker.rerank(query, passages))

    scored_list: List[ScoredChunk] = []
    for candidate, score in zip(candidates, raw_scores):
        scored_list.append(
            ScoredChunk(
                chunk_id=str(candidate.get("id", "")),
                text=candidate.get("text", ""),
                citation=candidate.get("citation", ""),
                section=candidate.get("section", ""),
                department=candidate.get("department", ""),
                clearance_level=candidate.get("clearance_level", ""),
                score=float(score),
                payload=candidate,
            )
        )

    # Sort descending by neural score
    scored_list.sort(key=lambda x: x.score, reverse=True)
    return scored_list[:top_k]
