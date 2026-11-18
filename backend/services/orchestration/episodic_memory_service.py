from datetime import datetime, timezone
import logging
from typing import Any, Dict, List, Optional
import uuid
from pydantic import BaseModel, Field
from qdrant_client import QdrantClient, models

from backend.core.config import settings
from backend.services.orchestration.llm_service import llm_service
from backend.services.vector.embedding_service import generate_dense_embeddings

logger = logging.getLogger(__name__)

# Minimum cosine similarity to consider two memories semantically identical / conflicting
DEDUPLICATION_SIMILARITY_THRESHOLD = 0.85
# Minimum cosine similarity to retrieve a relevant memory during query execution
RETRIEVAL_SIMILARITY_THRESHOLD = 0.55


class ExtractedMemoryFact(BaseModel):
    fact: str = Field(description="A concise, factual persistent user attribute, preference, project context, or habit")
    category: str = Field(
        default="general",
        description="Category: 'preference', 'tech_stack', 'project_context', 'location', or 'general' (for noteworthy personal facts that don't fit other categories)",
    )


class MemoryExtractionSchema(BaseModel):
    facts: List[ExtractedMemoryFact] = Field(
        default_factory=list,
        description="List of extracted persistent user facts. If message contains no personal facts, return empty list.",
    )


class EpisodicMemoryService:
    """
    Tier 2 Archival / Episodic Memory Engine (Cold LTM).
    Uses Qdrant collection 'cortex_user_memories' + FastEmbed dense vectors (384d):
      1. On-Demand Semantic Search: Fetches relevant past user facts at runtime.
      2. Autonomous Fact Extraction: Uses Fast LLM to identify persistent user traits.
      3. Semantic Deduplication: Searches Qdrant before insertion to prevent duplicates.
      4. User Management: Allows viewing and deleting memories for privacy compliance.
    """

    def __init__(self, collection_name: Optional[str] = None) -> None:
        self.collection_name = collection_name or settings.QDRANT_USER_MEMORIES_COLLECTION

    async def get_user_preferences(
        self,
        client: Optional[QdrantClient],
        user_id: uuid.UUID,
        limit: int = 3,
    ) -> List[str]:
        """Fast payload-only lookup of persistent user communication/formatting preferences (no embedding needed)."""
        if client is None:
            from backend.db.qdrant import get_qdrant_client
            client = get_qdrant_client()
        pref_filter = models.Filter(
            must=[
                models.FieldCondition(key="user_id", match=models.MatchValue(value=str(user_id))),
                models.FieldCondition(key="category", match=models.MatchValue(value="preference")),
            ]
        )
        try:
            scroll_res = client.scroll(
                collection_name=self.collection_name,
                scroll_filter=pref_filter,
                limit=limit,
                with_payload=True,
                with_vectors=False,
            )
            points = scroll_res[0] if scroll_res else []
            return [
                str(p.payload["fact"])
                for p in points
                if p.payload and p.payload.get("fact")
            ]
        except Exception:
            return []

    async def search_memories(
        self,
        client: Optional[QdrantClient],
        user_id: uuid.UUID,
        query: str,
        limit: int = 3,
        threshold: float = RETRIEVAL_SIMILARITY_THRESHOLD,
    ) -> List[str]:
        """
        Retrieves relevant archival memories for a user on-demand (~30-50ms).
        Filters strictly by user_id to guarantee zero cross-tenant leakage.
        """
        if not query.strip():
            return []

        if client is None:
            from backend.db.qdrant import get_qdrant_client
            client = get_qdrant_client()

        embeddings = generate_dense_embeddings([query])
        if not embeddings:
            return []
        query_vector = embeddings[0]

        user_filter = models.Filter(
            must=[
                models.FieldCondition(
                    key="user_id",
                    match=models.MatchValue(value=str(user_id)),
                )
            ]
        )

        try:
            results = client.query_points(
                collection_name=self.collection_name,
                query=query_vector,
                query_filter=user_filter,
                limit=limit,
                score_threshold=threshold,
            )
            points = results.points if hasattr(results, "points") else results
            memories: List[str] = []
            for hit in points:
                payload = hit.payload or {}
                fact_text = payload.get("fact")
                if fact_text and fact_text not in memories:
                    memories.append(fact_text)
            return memories
        except Exception as e:
            logger.warning("Error searching user memories in Qdrant (%s); continuing without cold LTM", e)
            return []

    async def _store_single_fact(
        self,
        client: QdrantClient,
        user_id: uuid.UUID,
        fact_text: str,
        category: str = "preference",
    ) -> Optional[str]:
        fact_text = fact_text.strip()
        if not fact_text or len(fact_text) < 4:
            return None

        existing_similar = await self.search_memories(
            client=client,
            user_id=user_id,
            query=fact_text,
            limit=1,
            threshold=DEDUPLICATION_SIMILARITY_THRESHOLD,
        )
        if existing_similar:
            logger.info("Memory fact already exists for user %s: '%s' (skipping duplicate)", user_id, fact_text)
            return None

        embeddings = generate_dense_embeddings([fact_text])
        if not embeddings:
            return None

        point_id = str(uuid.uuid4())
        payload = {
            "user_id": str(user_id),
            "fact": fact_text,
            "category": category or "general",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        client.upsert(
            collection_name=self.collection_name,
            points=[
                models.PointStruct(
                    id=point_id,
                    vector=embeddings[0],
                    payload=payload,
                )
            ],
        )
        logger.info("Stored new Archival Memory for user %s: '%s'", user_id, fact_text)
        return fact_text

    async def extract_and_store_memories(
        self,
        client: Optional[QdrantClient],
        user_id: uuid.UUID,
        user_message: str,
        recent_user_message: Optional[str] = None,
    ) -> List[str]:
        """
        Autonomously extracts new persistent facts from user dialogue turns,
        runs semantic deduplication in Qdrant, and commits novel facts.
        Handles explicit memory requests ('add to memories', 'remember this') by
        combining with the immediately preceding user message when needed.
        """
        import re

        if not user_message or not user_message.strip():
            return []

        if client is None:
            from backend.db.qdrant import get_qdrant_client
            client = get_qdrant_client()

        explicit_mem_cmd = bool(re.search(r"\b(add+\s+to\s+memo?r|save\s+to\s+memo?r|remember\s+(that|this|me)|note\s+that\s+i)\b", user_message, re.IGNORECASE))
        # Strip explicit command suffix/prefix to inspect if the message itself carries the fact
        cleaned_explicit = re.sub(
            r"[,.;!\s]*\b(please\s+|pls\s+)?(add+\s+(this\s+|that\s+|it\s+)?to\s+(my\s+)?memo?r(?:y|ies)|save\s+(this\s+|that\s+|it\s+)?to\s+(my\s+)?memo?r(?:y|ies)|remember\s+(that|this))\b[,.;!\s]*",
            " ",
            user_message,
            flags=re.IGNORECASE,
        ).strip()

        # If the user only said "add to memories" referring to their previous turn, pull previous turn in!
        effective_input = user_message
        if explicit_mem_cmd and len(cleaned_explicit) < 8 and recent_user_message:
            effective_input = f"{recent_user_message.strip()} (User explicitly asked: {user_message.strip()})"
            cleaned_explicit = recent_user_message.strip()

        system_prompt = (
            "You are Cortex's Episodic Memory Extractor. "
            "Analyze the user's message to identify if they shared any persistent personal facts, "
            "communication/formatting preferences (e.g. preferring diagrams, mermaid diagrams, code snippets, concise answers), "
            "workplace preferences, office locations, project responsibilities, or technical preferences "
            "(e.g., 'I work on the payments service', 'My base office is Pune', 'I prefer Python for backend').\n\n"
            "Rules:\n"
            "1. ONLY extract clear, durable facts or preferences about the USER (clean up typos into a crisp sentence).\n"
            "2. Assign each fact a category: 'preference', 'tech_stack', 'project_context', 'location', or 'general'.\n"
            "3. If the user explicitly asked to remember or add something to memories, ALWAYS extract it.\n"
            "4. If no persistent facts or preferences are stated, return an empty list."
        )

        saved_facts: List[str] = []
        llm_failed = False
        try:
            extracted = await llm_service.generate_structured(
                prompt=f"User Message: {effective_input}",
                response_model=MemoryExtractionSchema,
                system_prompt=system_prompt,
                model=settings.FAST_ROUTING_MODEL,
            )
            if extracted and extracted.facts:
                for item in extracted.facts:
                    stored = await self._store_single_fact(client, user_id, item.fact, item.category)
                    if stored:
                        saved_facts.append(stored)
        except Exception as e:
            llm_failed = True
            logger.warning("Error in extract_and_store_memories LLM extraction (%s)", e)

        # Deterministic safety net: if user explicitly asked to save a preference/memory
        # (or clearly stated 'I like / I prefer ...') and LLM extraction failed or returned nothing, store cleanly.
        if not saved_facts and (explicit_mem_cmd or (llm_failed and re.search(r"\b(i\s+like|i\s+prefer|my\s+preference)\b", user_message, re.IGNORECASE))):
            fallback_fact = cleaned_explicit if len(cleaned_explicit) >= 6 else user_message.strip()
            if len(fallback_fact) >= 6:
                try:
                    stored = await self._store_single_fact(client, user_id, fallback_fact, "preference")
                    if stored:
                        saved_facts.append(stored)
                except Exception as fb_err:
                    logger.warning("Fallback memory store failed (%s)", fb_err)

        return saved_facts

    async def list_memories_page(
        self,
        client: QdrantClient,
        user_id: uuid.UUID,
        limit: int = 50,
        offset: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Cursor-paginated memory listing scoped to one user."""
        user_filter = models.Filter(
            must=[models.FieldCondition(key="user_id", match=models.MatchValue(value=str(user_id)))]
        )
        try:
            count_result = client.count(
                collection_name=self.collection_name,
                count_filter=user_filter,
                exact=True,
            )
            scroll_res = client.scroll(
                collection_name=self.collection_name,
                scroll_filter=user_filter,
                offset=offset,
                limit=limit,
                with_payload=True,
                with_vectors=False,
            )
            points = scroll_res[0] if scroll_res else []
            next_offset = scroll_res[1] if scroll_res and len(scroll_res) > 1 else None
            return {
                "items": [
                    {
                        "id": str(point.id),
                        "fact": (point.payload or {}).get("fact"),
                        "category": (point.payload or {}).get("category"),
                        "created_at": (point.payload or {}).get("created_at"),
                    }
                    for point in points
                    if point.payload
                ],
                "total": int(getattr(count_result, "count", 0)),
                "next_cursor": str(next_offset) if next_offset is not None else None,
            }
        except Exception:
            logger.exception("Could not list personal memories for user_id=%s", user_id)
            raise

    async def list_memories(
        self,
        client: QdrantClient,
        user_id: uuid.UUID,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """Backward-compatible first-page memory listing."""
        result = await self.list_memories_page(client=client, user_id=user_id, limit=limit)
        return result["items"]

    async def update_memory(
        self,
        client: Optional[QdrantClient],
        user_id: uuid.UUID,
        memory_id: str,
        new_text: str,
        category: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Updates a specific memory owned by the user and re-embeds its text."""
        clean_text = (new_text or "").strip()
        if not clean_text:
            return None
        if client is None:
            from backend.db.qdrant import get_qdrant_client
            client = get_qdrant_client()

        try:
            points = client.retrieve(
                collection_name=self.collection_name,
                ids=[memory_id],
                with_payload=True,
            )
            if not points or not points[0].payload or points[0].payload.get("user_id") != str(user_id):
                return None

            existing_payload = dict(points[0].payload)
            embeddings = generate_dense_embeddings([clean_text])
            if not embeddings:
                return None

            updated_payload = {
                "user_id": str(user_id),
                "fact": clean_text,
                "category": (category or existing_payload.get("category") or "general").strip(),
                "created_at": existing_payload.get("created_at") or datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            client.upsert(
                collection_name=self.collection_name,
                points=[
                    models.PointStruct(
                        id=memory_id,
                        vector=embeddings[0],
                        payload=updated_payload,
                    )
                ],
            )
            logger.info("Updated memory point %s for user %s", memory_id, user_id)
            return {
                "memory_id": memory_id,
                "text": updated_payload["fact"],
                "category": updated_payload["category"],
                "created_at": updated_payload["created_at"],
            }
        except Exception as e:
            logger.warning("Error updating memory (%s)", e)
            return None

    async def delete_memory(
        self,
        client: Optional[QdrantClient],
        user_id: uuid.UUID,
        memory_id: str,
    ) -> bool:
        """Deletes a specific memory point for privacy compliance."""
        if client is None:
            from backend.db.qdrant import get_qdrant_client
            client = get_qdrant_client()

        try:
            # Verify ownership
            point = client.retrieve(
                collection_name=self.collection_name,
                ids=[memory_id],
                with_payload=True,
            )
            if not point or not point[0].payload or point[0].payload.get("user_id") != str(user_id):
                return False

            client.delete(
                collection_name=self.collection_name,
                points_selector=models.PointIdsList(points=[memory_id]),
            )
            logger.info("Deleted memory point %s for user %s", memory_id, user_id)
            return True
        except Exception as e:
            logger.warning("Error deleting memory (%s)", e)
            return False


# Global singleton instance
episodic_memory_service = EpisodicMemoryService()
