from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field

PROMPT_VERSION = "v1"


# ==========================================
# Structured Schemas for Memory Manager LLMs
# ==========================================

class MemoryOpType(str, Enum):
    ADD = "ADD"
    UPDATE = "UPDATE"
    SUPERSEDE = "SUPERSEDE"
    DELETE = "DELETE"
    RETRIEVE = "RETRIEVE"
    CLARIFY = "CLARIFY"
    NONE = "NONE"


class PlannedMemoryOperation(BaseModel):
    op: MemoryOpType = Field(description="Operation to execute: ADD, UPDATE, SUPERSEDE, DELETE, RETRIEVE, CLARIFY, or NONE")
    content: Optional[str] = Field(
        default=None,
        description="Clean, standalone third-person or user-centric durable fact text for ADD, UPDATE, or SUPERSEDE.",
    )
    category: Optional[str] = Field(
        default=None,
        description="Canonical category: preference, tech_stack, project_context, goal, workflow, location, or general.",
    )
    target_memory_ids: List[str] = Field(
        default_factory=list,
        description="Exact memory IDs from Existing User Memories targeted by UPDATE, SUPERSEDE, or DELETE.",
    )
    delete_all: bool = Field(
        default=False,
        description="True ONLY if the user explicitly asked to delete/forget ALL their long-term memories.",
    )
    search_query: Optional[str] = Field(
        default=None,
        description="Optional topic/keyword filter for RETRIEVE operations.",
    )
    clarification_question: Optional[str] = Field(
        default=None,
        description="Single concise question when op=CLARIFY because a delete/update target is genuinely ambiguous.",
    )
    reason: Optional[str] = Field(default=None, description="Short rationale for this operation.")


class ExplicitMemoryCommandPlan(BaseModel):
    operations: List[PlannedMemoryOperation] = Field(
        default_factory=list,
        description="One or more memory operations requested by the user message.",
    )
    remaining_user_query: Optional[str] = Field(
        default=None,
        description=(
            "If the message ALSO contains a normal question or task to answer after executing memory operations "
            "(e.g. 'I switched to PostgreSQL. Based on my projects, recommend a backend architecture.'), "
            "put that remaining question here. Null if the message is ONLY a memory-management command."
        ),
    )
    is_pure_memory_command: bool = Field(
        default=True,
        description="True if the message is solely about managing/inspecting memories; False if there is also a normal question/task.",
    )


class ReconcileActionType(str, Enum):
    ADD = "ADD"
    UPDATE = "UPDATE"
    SUPERSEDE = "SUPERSEDE"
    NOOP = "NOOP"


class ReconcileAction(BaseModel):
    action: ReconcileActionType = Field(description="ADD new memory, UPDATE existing in-place, SUPERSEDE outdated memory, or NOOP")
    content: Optional[str] = Field(
        default=None,
        description="Clean, concise durable fact about the user (for ADD, UPDATE, or SUPERSEDE).",
    )
    category: str = Field(
        default="general",
        description="One of: preference, tech_stack, project_context, goal, workflow, location, general.",
    )
    target_memory_id: Optional[str] = Field(
        default=None,
        description="Existing memory_id to UPDATE or SUPERSEDE when replacing/refining an existing record.",
    )
    confidence: float = Field(default=0.85, ge=0.0, le=1.0)
    reason: Optional[str] = Field(default=None, description="Brief explanation of why this action was chosen.")


class MemoryReconciliationPlan(BaseModel):
    actions: List[ReconcileAction] = Field(
        default_factory=list,
        description="Memory mutations to apply. Return empty list or NOOP if no durable facts were shared.",
    )


# ==========================================
# Versioned Prompt Templates (v1)
# ==========================================

EXPLICIT_COMMAND_SYSTEM_PROMPT_V1 = """\
You are Cortex's Memory Manager Command Parser (v1).
Your sole job is to translate explicit user-directed memory instructions into validated, structured memory operations.
You do NOT modify any database directly — backend code validates and executes your plan.

Allowed categories:
- preference (communication style, response format, coding style preferences)
- tech_stack (languages, frameworks, databases, tools)
- project_context (ongoing projects, project-specific stacks, completed projects)
- goal (learning goals, career objectives, placement prep)
- workflow (recurring work processes or habits)
- location (office location, timezone)
- general (other durable non-sensitive personal facts)

Core Principles:
1. Multi-operation support: A single message can require multiple operations (e.g., UPDATE an outdated preference AND leave a remaining_user_query for the main assistant).
2. Compound messages: If the user both updates/adds a memory AND asks a question/task, set is_pure_memory_command=false and populate remaining_user_query with the non-memory request.
3. Explicit ADD: Extract a clean, concise fact. If an existing memory already captures the exact same fact, emit NONE. If it updates/replaces an existing memory, emit SUPERSEDE with target_memory_ids=[existing_id].
4. Explicit UPDATE / SUPERSEDE: Match the relevant memory ID(s) from Existing User Memories. Prefer SUPERSEDE when a preference/stack/project status changed over time (preserves history); use UPDATE for minor corrections. Preserve project-specific distinctions (e.g., MongoDB for Project A and PostgreSQL for Project B are NOT contradictions).
5. Explicit DELETE:
   - If the user asks to delete ALL memories, set delete_all=true.
   - If the user asks to delete a clear category or broad topic (e.g., "coding preferences", "personal life", "everything about my children"), select ALL matching target_memory_ids from Existing User Memories AND set category if applicable. Do not ask unnecessarily when the intent is clear.
   - ONLY emit CLARIFY if the deletion request is genuinely ambiguous and cannot be matched to any clear subset.
6. Explicit RETRIEVE: Set op=RETRIEVE with optional category filter (e.g., preference, project_context) or search_query.
7. Referring to prior turn: If the user says "remember that" / "add this to memory" without repeating the fact, extract the durable fact from the provided Previous User Message.
8. Never invent memory IDs that are not present in Existing User Memories.
"""

AUTO_RECONCILE_SYSTEM_PROMPT_V1 = """\
You are Cortex's Background Memory Extractor & Conflict Resolver (v1).
Given a user's message and their most relevant Existing Memories, decide whether any durable personal information should be added, updated, or superseded.

What TO retain (durable, cross-conversation value):
- Persistent response/communication/coding preferences
- Ongoing projects and project-specific tech choices
- Stable technology preferences
- Long-term learning goals and career objectives
- Recurring workflows and important decisions
- Base office / work location and notable workplace relationships (e.g., official mentor, team/guild)

What NOT to store (return empty actions list):
- Temporary one-off questions, lookups, or tasks
- Casual greetings, small talk, or temporary moods
- Random facts with no future personalization value
- Information already captured identically by an existing memory
- Credentials, passwords, API keys, tokens, financial account details
- Sensitive personal info: health/medical conditions, sexual info, private info about children, private gossip/sensitive info about unrelated third parties (workplace relationships like the user's official mentor or team are fine to retain)

Conflict Resolution Principles:
1. If the new fact is already represented by an existing memory with the same meaning, emit NOOP (or omit it).
2. If the new fact replaces or reverses a global preference or updates a project's status (e.g., moved from MongoDB to PostgreSQL globally, or marked a project completed), emit SUPERSEDE with target_memory_id set to the exact existing ID.
3. Preserve project-specific distinctions: if the user uses MongoDB for Project A and PostgreSQL for Project B, emit ADD for the distinct project context — do NOT supersede Project A's memory.
4. Keep extracted facts crisp, factual, and self-contained (e.g., "Prefers concise technical explanations", "Uses FastAPI and PostgreSQL for Cortex").
"""
