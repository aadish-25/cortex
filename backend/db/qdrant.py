import logging
from typing import Optional

from qdrant_client import QdrantClient, models
from qdrant_client.http.exceptions import UnexpectedResponse

from backend.core.config import settings

logger = logging.getLogger(__name__)

# Cached singleton clients keyed by (url, api_key) to avoid creating a new HTTP pool
# and running a blocking GET /collections probe on every single API request.
_CLIENT_CACHE: dict[tuple[str, Optional[str]], QdrantClient] = {}


def reset_qdrant_client_cache() -> None:
    """Clears cached network Qdrant clients (primarily for test isolation)."""
    _CLIENT_CACHE.clear()


def get_qdrant_client(url: Optional[str] = None, *, force_probe: bool = False) -> QdrantClient:
    """
    Client factory for Qdrant vector database.
    - If URL is explicitly ':memory:', returns a fresh isolated in-memory client.
    - Otherwise returns a pooled singleton client for `(url, api_key)`, probing
      connectivity on first connect (or when `force_probe=True`).
    - If Qdrant is unreachable, raises ConnectionError with an actionable message.
    """
    target_url = url or settings.QDRANT_URL

    if target_url == ":memory:":
        return QdrantClient(":memory:")

    api_key = settings.QDRANT_API_KEY or None
    cache_key = (target_url, api_key)
    cached = _CLIENT_CACHE.get(cache_key)
    if cached is not None and not force_probe:
        return cached

    try:
        client = cached or QdrantClient(
            url=target_url,
            api_key=api_key,
            timeout=5,
        )
        # Probe server connectivity on initial connect or explicit health probe
        client.get_collections()
        _CLIENT_CACHE[cache_key] = client
        return client
    except Exception as exc:
        _CLIENT_CACHE.pop(cache_key, None)
        err_msg = (
            f"Could not connect to Qdrant vector database at '{target_url}'. "
            "Please ensure Docker Desktop is running and launch required containers "
            f"via 'python run.py' or 'docker compose up -d'. Details: {exc}"
        )
        logger.error(err_msg)
        raise ConnectionError(err_msg) from exc


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


def init_user_memories_collection(client: QdrantClient, collection_name: Optional[str] = None) -> None:
    """Initializes the archival user memories collection in Qdrant (Tier 2 Cold LTM)."""
    target_collection = collection_name or settings.QDRANT_USER_MEMORIES_COLLECTION

    if not client.collection_exists(target_collection):
        client.create_collection(
            collection_name=target_collection,
            vectors_config=models.VectorParams(
                size=384,
                distance=models.Distance.COSINE,
            ),
        )
        logger.info("Created Qdrant user memories collection: %s", target_collection)

    for field_name in ("user_id", "category", "status"):
        try:
            client.create_payload_index(
                collection_name=target_collection,
                field_name=field_name,
                field_schema=models.PayloadSchemaType.KEYWORD,
            )
        except UnexpectedResponse:
            pass
