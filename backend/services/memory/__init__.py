"""Lazy re-exports for the independent memory package (PEP 562).

Keeps lightweight consumers (e.g. Celery sweeper importing `memory_store`) from
eagerly loading `memory_manager` -> `llm_service` -> `litellm`.
"""

from importlib import import_module
from typing import Any

_EXPORTS = {
    "ExplicitCommandSignal": ("backend.services.memory.command_detector", "ExplicitCommandSignal"),
    "detect_explicit_memory_command": ("backend.services.memory.command_detector", "detect_explicit_memory_command"),
    "has_broad_personal_cue": ("backend.services.memory.command_detector", "has_broad_personal_cue"),
    "is_trivial_message": ("backend.services.memory.command_detector", "is_trivial_message"),
    "should_attempt_background_extraction": ("backend.services.memory.command_detector", "should_attempt_background_extraction"),
    "MemoryCommandResult": ("backend.services.memory.memory_manager", "MemoryCommandResult"),
    "MemoryManagerService": ("backend.services.memory.memory_manager", "MemoryManagerService"),
    "memory_manager": ("backend.services.memory.memory_manager", "memory_manager"),
    "MemoryStore": ("backend.services.memory.memory_store", "MemoryStore"),
    "MemoryVersionConflictError": ("backend.services.memory.memory_store", "MemoryVersionConflictError"),
    "memory_store": ("backend.services.memory.memory_store", "memory_store"),
    "MemoryValidationResult": ("backend.services.memory.privacy_guard", "MemoryValidationResult"),
    "sanitize_memory_content": ("backend.services.memory.privacy_guard", "sanitize_memory_content"),
    "validate_memory_content": ("backend.services.memory.privacy_guard", "validate_memory_content"),
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
