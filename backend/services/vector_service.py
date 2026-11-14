import logging
from typing import List, Optional
import uuid

from qdrant_client import QdrantClient, models

from backend.core.config import settings
from backend.models.enums import ClearanceLevel
from backend.db.qdrant import init_qdrant_collection
from backend.services.chunking_service import DocumentChunk
from backend.services.embedding_service import (
    generate_dense_embeddings,
    generate_sparse_embeddings,
)

logger = logging.getLogger(__name__)


def index_document_chunks(
    client: QdrantClient,
    document_id: uuid.UUID,
    version: int,
    department: str,
    clearance_level: ClearanceLevel,
    chunks: List[DocumentChunk],
    is_live: bool = False,
    collection_name: Optional[str] = None,
) -> int:
    """
    Embeds and stores document chunks in Qdrant (Decisions D4 & D6).
    - Uses stamped_content (D6 Identity Header) for vector math.
    - Staged version flip: default is_live=False keeps chunks invisible until activated (D4).
    Returns total points indexed.
    """
    if not chunks:
        return 0

    target_collection = collection_name or settings.QDRANT_COLLECTION
    init_qdrant_collection(client, collection_name=target_collection)

    # 1. Embed using the Decision D6 stamped content (anchors title, version, dept)
    stamped_texts = [c.stamped_content for c in chunks]
    dense_vectors = generate_dense_embeddings(stamped_texts)
    sparse_vectors = generate_sparse_embeddings(stamped_texts)

    # 2. Build Qdrant PointStruct records
    points: List[models.PointStruct] = []
    for idx, chunk in enumerate(chunks):
        point_id = str(uuid.uuid4())
        payload = {
            "document_id": str(document_id),
            "version": version,
            "department": department,
            "clearance_level": clearance_level.value,
            "section": chunk.section,
            "page_number": chunk.page_number,
            "citation": chunk.citation,
            "text": chunk.text,
            "stamped_content": chunk.stamped_content,
            "is_live": is_live,  # Staged version flip: False until Qdrant confirms full index
        }

        points.append(
            models.PointStruct(
                id=point_id,
                vector={
                    "dense": dense_vectors[idx],
                    "bm25": sparse_vectors[idx],
                },
                payload=payload,
            )
        )

    # 3. Batch upsert into Qdrant
    client.upsert(
        collection_name=target_collection,
        points=points,
    )
    logger.info(
        "Indexed %d chunks for document %s (v%d, is_live=%s)",
        len(points),
        document_id,
        version,
        is_live,
    )
    return len(points)


def activate_document_version(
    client: QdrantClient,
    document_id: uuid.UUID,
    active_version: int,
    collection_name: Optional[str] = None,
) -> None:
    """
    Atomic version flip switchover (Decision D4):
    1. Sets is_live = True for all chunks of active_version.
    2. Purges (deletes) all older chunks for this document.
    """
    target_collection = collection_name or settings.QDRANT_COLLECTION
    doc_id_str = str(document_id)

    # Step 1: Flip active version chunks to is_live = True
    client.set_payload(
        collection_name=target_collection,
        payload={"is_live": True},
        points=models.Filter(
            must=[
                models.FieldCondition(
                    key="document_id",
                    match=models.MatchValue(value=doc_id_str),
                ),
                models.FieldCondition(
                    key="version",
                    match=models.MatchValue(value=active_version),
                ),
            ]
        ),
    )

    # Step 2: Delete old version chunks (version != active_version)
    client.delete(
        collection_name=target_collection,
        points_selector=models.FilterSelector(
            filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key="document_id",
                        match=models.MatchValue(value=doc_id_str),
                    ),
                ],
                must_not=[
                    models.FieldCondition(
                        key="version",
                        match=models.MatchValue(value=active_version),
                    ),
                ],
            )
        ),
    )
    logger.info(
        "Activated document %s v%d: is_live flipped to True, older versions purged.",
        document_id,
        active_version,
    )
