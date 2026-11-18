"""Lazy re-exports across service subdomains (PEP 562).

Avoids eagerly importing heavy ML/LLM libraries (litellm, fastembed, pdfplumber)
into processes like Celery workers or lightweight CLI/test invocations.
"""

from importlib import import_module
from typing import Any

_EXPORTS = {
    "audit_service": ("backend.services.auth", "audit_service"),
    "auth_service": ("backend.services.auth", "auth_service"),
    "chunking_service": ("backend.services.ingestion", "chunking_service"),
    "document_service": ("backend.services.ingestion", "document_service"),
    "ingestion_service": ("backend.services.ingestion", "ingestion_service"),
    "embedding_service": ("backend.services.vector", "embedding_service"),
    "vector_service": ("backend.services.vector", "vector_service"),
    "crag_service": ("backend.services.retrieval", "crag_service"),
    "reranker_service": ("backend.services.retrieval", "reranker_service"),
    "retrieval_service": ("backend.services.retrieval", "retrieval_service"),
    "updating_policy_probe": ("backend.services.retrieval", "updating_policy_probe"),
    "dag_orchestrator": ("backend.services.orchestration", "dag_orchestrator"),
    "query_planner": ("backend.services.orchestration", "query_planner"),
    "web_search_service": ("backend.services.orchestration", "web_search_service"),
    "action_guard": ("backend.services.orchestration", "action_guard"),
    "action_engine": ("backend.services.orchestration", "action_engine"),
    "memory_service": ("backend.services.orchestration", "memory_service"),
    "jira_service": ("backend.services.integrations", "jira_service"),
}

__all__ = list(_EXPORTS.keys())


def __getattr__(name: str) -> Any:
    if name in _EXPORTS:
        module_path, attr = _EXPORTS[name]
        module = import_module(module_path)
        value = getattr(module, attr)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
