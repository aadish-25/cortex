import logging
from typing import List, Optional

from fastembed import SparseTextEmbedding, TextEmbedding
from qdrant_client import models

from backend.core.config import settings

logger = logging.getLogger(__name__)

# Singletons for FastEmbed models (prevents reloading ONNX weights on each call)
_dense_model: Optional[TextEmbedding] = None
_sparse_model: Optional[SparseTextEmbedding] = None


def get_dense_model() -> TextEmbedding:
    """Lazy-loads the FastEmbed dense embedding model (BAAI/bge-small-en-v1.5, 384d)."""
    global _dense_model
    if _dense_model is None:
        _dense_model = TextEmbedding(model_name=settings.EMBEDDING_MODEL)
    return _dense_model


def get_sparse_model() -> SparseTextEmbedding:
    """Lazy-loads the FastEmbed BM25 sparse lexical model."""
    global _sparse_model
    if _sparse_model is None:
        _sparse_model = SparseTextEmbedding(model_name="Qdrant/bm25")
    return _sparse_model


def generate_dense_embeddings(texts: List[str]) -> List[List[float]]:
    """
    Generates 384-dimensional dense vectors using FastEmbed BGE-small.
    Returns a list of float arrays, ready for Qdrant cosine similarity search.
    """
    if not texts:
        return []
    model = get_dense_model()
    embeddings = list(model.embed(texts))
    return [vec.tolist() for vec in embeddings]


def generate_sparse_embeddings(texts: List[str]) -> List[models.SparseVector]:
    """
    Generates BM25 lexical sparse vectors using FastEmbed.
    Encapsulates dictionary word indices and term weights for Qdrant keyword matching.
    """
    if not texts:
        return []
    model = get_sparse_model()
    embeddings = list(model.embed(texts))
    return [
        models.SparseVector(
            indices=vec.indices.tolist(),
            values=vec.values.tolist(),
        )
        for vec in embeddings
    ]
