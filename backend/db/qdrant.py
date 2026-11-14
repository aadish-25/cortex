import logging
from typing import Optional

from qdrant_client import QdrantClient, models
from qdrant_client.http.exceptions import UnexpectedResponse

from backend.core.config import settings

logger = logging.getLogger(__name__)


def get_qdrant_client(url: Optional[str] = None) -> QdrantClient:
    """
    Client factory for Qdrant vector database.
    - If URL is ':memory:', runs an embedded in-memory instance (ideal for isolated testing).
    - Otherwise connects to settings.QDRANT_URL with timeout.
    - Gracefully falls back to embedded in-memory mode if remote server is unreachable during local dev.
    """
    target_url = url or settings.QDRANT_URL

    if target_url == ":memory:":
        return QdrantClient(":memory:")

    try:
        client = QdrantClient(
            url=target_url,
            api_key=settings.QDRANT_API_KEY or None,
            timeout=5,
        )
        # Probe server connectivity
        client.get_collections()
        return client
    except Exception as exc:
        logger.warning(
            "Could not connect to Qdrant at %s (%s). Falling back to embedded in-memory Qdrant.",
            target_url,
            exc,
        )
        return QdrantClient(":memory:")


def init_qdrant_collection(client: QdrantClient, collection_name: Optional[str] = None) -> None:
    """
    Initializes the Cortex vector collection in Qdrant with:
    1. Named Vectors: Dense (384d, Cosine) + Sparse BM25
    2. Payload indexes for 3D RBAC and staged version flip (Decisions D3, D4)
    """
    target_collection = collection_name or settings.QDRANT_COLLECTION

    if not client.collection_exists(target_collection):
        client.create_collection(
            collection_name=target_collection,
            vectors_config={
                "dense": models.VectorParams(
                    size=384,
                    distance=models.Distance.COSINE,
                )
            },
            sparse_vectors_config={
                "bm25": models.SparseVectorParams(),
            },
        )
        logger.info("Created Qdrant collection: %s with Dense + Sparse vectors", target_collection)

    # 3D RBAC payload field indexes for O(1) pre-filtering (Decision D3)
    indexes_to_create = [
        ("department", models.PayloadSchemaType.KEYWORD),
        ("clearance_level", models.PayloadSchemaType.KEYWORD),
        ("is_live", models.PayloadSchemaType.BOOL),
        ("document_id", models.PayloadSchemaType.KEYWORD),
        ("version", models.PayloadSchemaType.INTEGER),
    ]

    for field_name, schema_type in indexes_to_create:
        try:
            client.create_payload_index(
                collection_name=target_collection,
                field_name=field_name,
                field_schema=schema_type,
            )
        except UnexpectedResponse:
            pass
