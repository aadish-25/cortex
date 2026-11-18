import logging
from typing import Optional
import uuid

from qdrant_client import QdrantClient, models
from sqlmodel import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.config import settings
from backend.models.document import Document
from backend.models.enums import CLEARANCE_RANK, ClearanceLevel, DocumentStatus, Role
from backend.models.user import User
from backend.services.vector.vector_service import generate_dense_embeddings

logger = logging.getLogger(__name__)


def build_updating_probe_filter(user: User) -> models.Filter:
    """
    Constructs Qdrant boolean filter strictly for staged/updating chunks (is_live == False),
    while still enforcing user clearance and department bounds (Decision D3 & D9).
    """
    if user.role == Role.ADMIN:
        return models.Filter(
            must=[models.FieldCondition(key="is_live", match=models.MatchValue(value=False))]
        )

    allowed_clearances = [
        lvl.value
        for lvl, rank in CLEARANCE_RANK.items()
        if rank <= CLEARANCE_RANK[user.clearance_level]
    ]

    return models.Filter(
        must=[
            models.FieldCondition(key="is_live", match=models.MatchValue(value=False)),
            models.FieldCondition(
                key="clearance_level",
                match=models.MatchAny(any=allowed_clearances),
            ),
        ],
        should=[
            models.FieldCondition(key="department", match=models.MatchValue(value=user.department)),
            models.FieldCondition(key="clearance_level", match=models.MatchValue(value=ClearanceLevel.PUBLIC.value)),
        ],
    )


async def probe_updating_policies(
    client: QdrantClient,
    session: AsyncSession,
    user: User,
    query: str,
    top_k: int = 3,
) -> Optional[str]:
    """
    ACL-Gated Updating Policy Detection Probe (Decision D9):
    1. Searches Qdrant for non-live chunks (is_live == False) with 3D RBAC filters.
    2. Cross-references matched document IDs against SQL database for status == UPDATING.
    3. If found and user is authorized, returns a locked notification notice.
    4. If user is unauthorized, Qdrant prunes the points and None is returned (zero knowledge).
    """
    query_dense = generate_dense_embeddings([query])[0]
    probe_filter = build_updating_probe_filter(user)

    # Search staged chunks
    try:
        search_res = client.query_points(
            collection_name=settings.QDRANT_COLLECTION,
            query=query_dense,
            using="dense",
            query_filter=probe_filter,
            limit=top_k,
        )
    except Exception as exc:
        logger.warning("Updating policy probe failed: %s", exc)
        return None

    if not search_res.points:
        return None

    # Check if any matched candidate is in UPDATING status in SQL
    for point in search_res.points:
        doc_id_str = point.payload.get("document_id") if point.payload else None
        if not doc_id_str:
            continue

        try:
            target_uuid = uuid.UUID(doc_id_str)
        except (ValueError, TypeError):
            continue

        stmt = select(Document).where(Document.id == target_uuid)
        res = await session.execute(stmt)
        doc = res.scalars().first()

        if doc and doc.status == DocumentStatus.UPDATING:
            doc_title = doc.title or doc.filename
            logger.info("Updating-document guard triggered for user_id=%s document_id=%s", user.id, doc.id)
            return (
                f"Notice: The official policy regarding '{doc_title}' is currently undergoing an administrative update. "
                f"To prevent unauthorized dissemination of draft policies, retrieved information is restricted until the updated document is published."
            )

    return None
