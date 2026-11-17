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
    category: str = Field(description="Category: 'preference', 'tech_stack', 'project_context', 'location'")


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

    async def search_memories(
        self,
        client: QdrantClient,
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

    async def extract_and_store_memories(
        self,
        client: QdrantClient,
        user_id: uuid.UUID,
        user_message: str,
    ) -> List[str]:
        """
        Autonomously extracts new persistent facts from user dialogue turns,
        runs semantic deduplication in Qdrant, and commits novel facts.
        """
        # Fast heuristic: skip pure commands or trivial greetings
        if len(user_message.strip()) < 15:
            return []

        system_prompt = (
            "You are Cortex's Episodic Memory Extractor. "
            "Analyze the user's message to identify if they shared any persistent personal facts, "
            "workplace preferences, office locations, project responsibilities, or technical preferences "
            "(e.g., 'I work on the payments service', 'My base office is Pune', 'I prefer Python for backend', "
            "'I have a disability requiring ergonomic equipment').\n\n"
            "Rules:\n"
            "1. ONLY extract clear, durable facts about the USER. Do NOT extract transient questions or temporary moods.\n"
            "2. If no persistent facts are stated, return an empty list."
        )

        try:
            extracted = await llm_service.generate_structured(
                prompt=f"User Message: {user_message}",
                response_model=MemoryExtractionSchema,
                system_prompt=system_prompt,
                model=settings.FAST_ROUTING_MODEL,
            )
            if not extracted or not extracted.facts:
                return []

            saved_facts: List[str] = []
            for item in extracted.facts:
                fact_text = item.fact.strip()
                if not fact_text:
                    continue

                # 1. Semantic Deduplication Check
                existing_similar = await self.search_memories(
                    client=client,
                    user_id=user_id,
                    query=fact_text,
                    limit=1,
                    threshold=DEDUPLICATION_SIMILARITY_THRESHOLD,
                )

                if existing_similar:
                    logger.info("Memory fact already exists for user %s: '%s' (skipping duplicate)", user_id, fact_text)
                    continue

                # 2. Vectorize and Insert New Fact
                embeddings = generate_dense_embeddings([fact_text])
                if not embeddings:
                    continue

                point_id = str(uuid.uuid4())
                payload = {
                    "user_id": str(user_id),
                    "fact": fact_text,
                    "category": item.category,
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
                saved_facts.append(fact_text)
                logger.info("Stored new Archival Memory for user %s: '%s'", user_id, fact_text)

            return saved_facts

        except Exception as e:
            logger.warning("Error in extract_and_store_memories (%s)", e)
            return []

    async def list_memories(
        self,
        client: QdrantClient,
        user_id: uuid.UUID,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """Lists all stored archival memories for a given user."""
        user_filter = models.Filter(
            must=[
                models.FieldCondition(
                    key="user_id",
                    match=models.MatchValue(value=str(user_id)),
                )
            ]
        )
        try:
            scroll_res = client.scroll(
                collection_name=self.collection_name,
                scroll_filter=user_filter,
                limit=limit,
                with_payload=True,
            )
            points = scroll_res[0] if scroll_res else []
            return [
                {
                    "id": str(p.id),
                    "fact": p.payload.get("fact"),
                    "category": p.payload.get("category"),
                    "created_at": p.payload.get("created_at"),
                }
                for p in points
                if p.payload
            ]
        except Exception as e:
            logger.warning("Error listing user memories (%s)", e)
            return []

    async def delete_memory(
        self,
        client: QdrantClient,
        user_id: uuid.UUID,
        memory_id: str,
    ) -> bool:
        """Deletes a specific memory point for privacy compliance."""
        try:
            # Verify ownership
            point = client.retrieve(
                collection_name=self.collection_name,
                ids=[memory_id],
                with_payload=True,
            )
            if not point or point[0].payload.get("user_id") != str(user_id):
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
