"""Backward-compatible re-export for the modular `backend.services.memory.store` package."""

from backend.services.memory.store.repository import (
    MemoryStore,
    MemoryVersionConflictError,
    memory_store,
)

__all__ = [
    "MemoryStore",
    "MemoryVersionConflictError",
    "memory_store",
]
