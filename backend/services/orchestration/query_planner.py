import logging
import re
from typing import List, Optional

from pydantic import BaseModel, Field

from backend.core.config import settings
from backend.models.chat import ChatMessage, ChatRole, ExecutionPlan, PlanTask, PlanTaskType
from backend.services.orchestration.llm_service import llm_service

logger = logging.getLogger(__name__)

# Heuristic patterns identifying pronouns and relational anaphora in conversational follow-ups (Fallback only)
ANAPHORA_PATTERN = re.compile(
    r"\b(it|its|they|them|their|those|these|this|that|he|she|his|her)\b|"
    r"\b(what about|how about|can i claim that|the policy|the rule|the same|same for|as mentioned|above|previous)\b|"
    r"\.\.\.$",
    re.IGNORECASE,
)

# Multi-command delimiter pattern for offline fallback (handles ;, ,, |, +, /, "and then", "after that")
SPLIT_CONJUNCTIONS_PATTERN = re.compile(
    r"\s*(?:;\s*|,\s*|\band then\b|\band also\b|\bafter that\b|\bas well as\b|\|\s*|\+\s*|\/\s*)\s*",
    re.IGNORECASE,
)

# Conversational chit-chat and greetings patterns (Fallback only)
CHIT_CHAT_TRIGGERS = [
    "what's up", "whats up", "what you doing", "what are you doing",
    "how are you", "who are you", "what can you do", "hi", "hello",
    "hey", "good morning", "good evening", "good afternoon",
    "how's it going", "how is it going", "thanks", "thank you",
    "bye", "goodbye", "cool", "ok", "okay", "kya chal raha hai"
]


# ==========================================
# Pydantic Schemas for Structured LLM Outputs
# ==========================================

class LLMQueryRewrite(BaseModel):
    has_anaphora_or_missing_context: bool = Field(
        description="True if the user's follow-up question references past conversation, pronouns ('it', 'that', 'those'), or missing referents in any language."
    )
    rewritten_query: str = Field(
        description="The self-contained standalone question resolving all pronouns and missing context. If no rewrite needed or if it is chit-chat, keep original query."
    )
    is_chit_chat: bool = Field(
        default=False,
        description="True if the message is general chit-chat, greeting, or small talk in English or Hinglish (e.g. 'What's up?', 'Hello', 'Kya chal raha hai')."
    )


class LLMPlanTask(BaseModel):
    id: int = Field(description="Sequential task integer ID, e.g. 1, 2, 3")
    type: PlanTaskType = Field(
        description="Task intent type: INTERNAL_RAG for company knowledge, EXTERNAL_WEB for internet search, STRUCTURED_LOOKUP for SQL DB queries, ACTION_WORKFLOW for Jira workflows, GITHUB_SEARCH for code/PR/issue search, DRIVE_SEARCH for Google Drive/Docs search, ADMIN_OPERATION for explicitly requested Admin-only operational tools, DIRECT_CHAT for greetings/chit-chat"
    )
    query: str = Field(description="Focused query, action instruction, or message for this specific task")
    depends_on: List[int] = Field(default_factory=list, description="IDs of prerequisite tasks that must complete first")


class LLMExecutionPlan(BaseModel):
    tasks: List[LLMPlanTask] = Field(description="List of plan tasks to execute")


# ==========================================
# Helpers & Context Management
# ==========================================

def detect_anaphora_or_pronoun(query: Optional[str] = None) -> bool:
    """
    Heuristic check for relational references and pronouns (Fallback path).
    Guarded defensively against None or non-string inputs.
    """
    if not query or not isinstance(query, str):
        return False
    return bool(ANAPHORA_PATTERN.search(query.strip()))


def extract_recent_history_window(
    history: Optional[List[ChatMessage]] = None,
    max_messages: int = 4,
) -> List[ChatMessage]:
    """
    Extracts sliding window of conversation turns.
    Excludes system messages to avoid context bloat.
    """
    if not history:
        return []
    non_system = [m for m in history if m.role in (ChatRole.USER, ChatRole.ASSISTANT)]
    return non_system[-max_messages:]


def format_conversation_history(
    history: Optional[List[ChatMessage]] = None,
    max_turns: int = 10,
    summary: Optional[str] = None,
) -> str:
    """
    Formats the conversation history for the LLM with support for Rolling Compaction (Decision D28).
    - If a prior summary exists, it is prepended: '[Prior Conversation Summary: ...]'
    - Preserves up to max_turns recent messages to maintain long-range context without blinding the model.
    """
    parts: List[str] = []
    if summary:
        parts.append(f"[Prior Conversation Summary]: {summary}")

    if not history:
        return "\n".join(parts)

    non_system = [m for m in history if m.role in (ChatRole.USER, ChatRole.ASSISTANT)]
    recent = non_system[-(max_turns * 2):] if max_turns else non_system

    for m in recent:
        parts.append(f"{m.role.value.capitalize()}: {m.content}")

    return "\n".join(parts)


# ==========================================
# Query Rewriting & Planner Logic
# ==========================================

async def rewrite_query(
    query: str,
    history: Optional[List[ChatMessage]] = None,
    summary: Optional[str] = None,
) -> tuple[str, bool]:
    """
    Conversational Query Rewriting Node (PRD 6.2 & Decision D16):
    1. Turn 1 (history empty): 0ms Zero-Latency Bypass.
    2. Turn 2+ Pronoun Trigger Rule (Decision D16): Only queries containing pronouns or
       relational references (e.g. 'it', 'that', 'those', 'what about', 'same for') trigger
       rewriting. Standalone queries bypass rewriting entirely (0ms overhead).
    3. Targeted 2-Turn Window (Decision D16): Restricts history to strictly the last 2 turns
       (max 4 messages) to prevent context drift and hallucinations.
    Returns (resolved_query, was_rewritten).
    """
    # 1. Turn 1 Zero-Latency Bypass (0ms overhead)
    if not history:
        return query, False

    # 2. Strict Pronoun/Anaphora Trigger Guard (Decision D16)
    # If the user query is already self-contained with no relational anaphora, bypass rewriting
    if not detect_anaphora_or_pronoun(query):
        return query, False

    # 3. Targeted 2-Turn Window (Decision D16: strictly last 2 turns = max 4 messages)
    conversation_context = format_conversation_history(history, max_turns=2, summary=summary)
    if not conversation_context:
        return query, False

    # 4. Primary Path: LiteLLM Gateway
    if llm_service.is_configured():
        prompt = (
            "You are an expert conversational query rewriter.\n"
            "Given the conversation history and the user's latest follow-up question:\n"
            "1. Determine whether the query references past conversation, pronouns ('it', 'that', 'those'), or missing context in English or Hinglish.\n"
            "2. Rewrite the user's question into a self-contained, standalone question resolving all pronouns and missing entities.\n"
            "3. If it is general chit-chat, identify it as chit-chat and do not alter the query.\n\n"
            f"Conversation History (Last 2 Turns):\n{conversation_context}\n\n"
            f"User Follow-up Question: {query}\n"
        )
        try:
            parsed = await llm_service.generate_structured(
                prompt=prompt,
                response_model=LLMQueryRewrite,
                model=settings.FAST_ROUTING_MODEL,
            )
            if parsed and parsed.has_anaphora_or_missing_context and parsed.rewritten_query:
                return parsed.rewritten_query.strip(), True
            return query, False
        except Exception as exc:
            logger.warning("LLM query rewriting failed, falling back to heuristic fusion: %s", exc)

    # 5. Deterministic heuristic fallback (when offline or no API key configured)
    recent_turns = extract_recent_history_window(history, max_messages=4)
    last_user_msg = next((m for m in reversed(recent_turns) if m.role == ChatRole.USER), None)
    if last_user_msg:
        heuristic_query = f"{query} (in context of: {last_user_msg.content})"
        return heuristic_query, True

    return query, False

    return query, False


def classify_task_intent(query_text: Optional[str] = None) -> PlanTaskType:
    """Classifies task intent using enterprise heuristics (Offline Fallback)."""
    if not query_text or not isinstance(query_text, str):
        return PlanTaskType.INTERNAL_RAG

    q_lower = query_text.lower().strip()

    # Direct chit-chat / greetings check
    if any(q_lower.startswith(trigger) or trigger in q_lower for trigger in CHIT_CHAT_TRIGGERS) and len(query_text.split()) < 8:
        return PlanTaskType.DIRECT_CHAT

    if any(k in q_lower for k in ["leave balance", "my tickets", "ticket status", "department head", "manager for"]):
        return PlanTaskType.STRUCTURED_LOOKUP
    elif any(k in q_lower for k in ["draft", "raise ticket", "create ticket", "file complaint", "apply for leave", "submit dispute"]):
        return PlanTaskType.ACTION_WORKFLOW
    elif any(k in q_lower for k in ["github", "pull request", "repo", "pr #", "git commit", "codebase", "branch"]):
        return PlanTaskType.GITHUB_SEARCH
    elif any(k in q_lower for k in ["google drive", "shared drive", "gdoc", "drive file", "google doc"]):
        return PlanTaskType.DRIVE_SEARCH
    elif any(k in q_lower for k in ["external", "market price", "latest 2026", "web search", "google", "flight benchmark", "news"]):
        return PlanTaskType.EXTERNAL_WEB

    return PlanTaskType.INTERNAL_RAG


def decompose_compound_query_heuristic(query: Optional[str] = None) -> List[PlanTask]:
    """
    Rule-based decomposition for compound prompts (Decision D24 Offline Fallback).
    Detects conjunctions, commas, semicolons, vertical bars, plus signs, slashes.
    """
    if not query or not isinstance(query, str):
        return []

    cleaned_query = query.strip()
    if not cleaned_query:
        return []

    parts = [p.strip() for p in SPLIT_CONJUNCTIONS_PATTERN.split(cleaned_query) if p.strip()]

    if len(parts) <= 1:
        task_type = classify_task_intent(cleaned_query)
        return [PlanTask(id=1, type=task_type, query=cleaned_query, depends_on=[])]

    tasks: List[PlanTask] = []
    lookup_and_rag_ids = []

    for idx, part in enumerate(parts, start=1):
        task_type = classify_task_intent(part)

        if task_type == PlanTaskType.ACTION_WORKFLOW and lookup_and_rag_ids:
            tasks.append(
                PlanTask(
                    id=idx,
                    type=task_type,
                    query=part,
                    depends_on=list(lookup_and_rag_ids),
                )
            )
        else:
            tasks.append(
                PlanTask(
                    id=idx,
                    type=task_type,
                    query=part,
                    depends_on=[],
                )
            )
            lookup_and_rag_ids.append(idx)

    return tasks


async def plan_and_decompose_query(
    query: str,
    history: Optional[List[ChatMessage]] = None,
    summary: Optional[str] = None,
    user_role: Optional[str] = None,
) -> ExecutionPlan:
    """
    Master Query Planner (Phase 3.1 & Decision D24):
    1. Rewrites conversational query if Turn 2+ with anaphora in English or Hinglish.
    2. Decomposes prompt into an optimal DAG of sub-tasks with strict depends_on IDs using LiteLLM.
       Natively handles commas, semicolons, pipes (|), plus signs (+), slashes (/), and multi-lingual chaining.
    """
    history = history or []

    # Step 1: Turn-based query rewrite
    active_query, was_rewritten = await rewrite_query(query, history, summary=summary)

    # Admin operational intents are isolated from ordinary employee RAG requests.
    if user_role == "Admin":
        admin_intent = re.search(
            r"\b(system health|service health|infrastructure health|health of|audit logs?|audit trail|reindex|re-index|retry ingestion|document status|indexing status|redis status|qdrant status|postgres status|status of (?:redis|qdrant|postgres|database|system))\b",
            active_query,
            re.IGNORECASE,
        )
        if admin_intent:
            return ExecutionPlan(
                tasks=[PlanTask(id=1, type=PlanTaskType.ADMIN_OPERATION, query=active_query, depends_on=[])],
                original_query=query,
                rewritten_query=active_query if was_rewritten else None,
                was_rewritten=was_rewritten,
            )

    # Step 2: Multi-Query Decomposition with LiteLLM Abstraction Layer
    if llm_service.is_configured():
        prompt = (
            "You are an enterprise query planner and multi-intent DAG decomposition engine.\n"
            "Analyze the user's prompt (which may be in English, Hinglish, informal language, or mixed phrasing).\n"
            "Users may chain multiple intents using commas, semicolons, vertical bars ('|'), plus signs ('+'), slashes ('/'), conjunctions, or natural transitions.\n\n"
            "Intent types:\n"
            "- INTERNAL_RAG: Company policies, employee handbook, benefits, tech stack, travel & offsite rules, security clearance.\n"
            "- EXTERNAL_WEB: Public internet search for outside market data, flights, general news, tax benchmarks.\n"
            "- STRUCTURED_LOOKUP: Internal SQL lookups for user leave balances, ticket status, department managers.\n"
            "- ACTION_WORKFLOW: Drafting or submitting Jira service tickets, equipment orders, leave requests.\n"
            "- GITHUB_SEARCH: Search code repositories, view pull requests, issues, or inspect code files in GitHub. ONLY assign if the user explicitly mentions GitHub, repo, PRs, issues, commits, or codebase.\n"
            "- DRIVE_SEARCH: Search company Google Shared Drive, Google Docs, or fetch shared file contents. ONLY assign if the user explicitly mentions Google Drive, Drive, shared folder, or gdoc.\n"
            "- ADMIN_OPERATION: Inspect system health, audit logs, document status, or explicitly trigger reindex. Only use for authenticated Admin users and explicit operational requests.\n"
            "- DIRECT_CHAT: Greetings, conversational pleasantries ('What's up', 'kya chal raha hai', 'who are you'), or general banter.\n\n"
            f"Authenticated role: {user_role or 'Unknown'}. Do not assign ADMIN_OPERATION unless role is exactly Admin.\n"
            "Rules:\n"
            "- Decompose compound prompts into discrete, standalone sub-tasks.\n"
            "- Default knowledge retrieval ALWAYS routes to INTERNAL_RAG unless the user explicitly requests Drive or GitHub.\n"
            "- Independent tasks must have depends_on: [].\n"
            "- If the query is conversational chit-chat, assign DIRECT_CHAT with depends_on: [].\n"
            "- Action tasks that require context from RAG or SQL lookups must declare their prerequisite task IDs in depends_on.\n\n"
            f"User Prompt: {active_query}\n"
        )
        try:
            parsed = await llm_service.generate_structured(
                prompt=prompt,
                response_model=LLMExecutionPlan,
                model=settings.FAST_ROUTING_MODEL,
            )
            if parsed and parsed.tasks:
                parsed_tasks = [
                    PlanTask(
                        id=t.id,
                        type=t.type,
                        query=t.query,
                        depends_on=t.depends_on,
                    )
                    for t in parsed.tasks
                ]
                return ExecutionPlan(
                    tasks=parsed_tasks,
                    original_query=query,
                    rewritten_query=active_query if was_rewritten else None,
                    was_rewritten=was_rewritten,
                )
        except Exception as exc:
            logger.warning("LLM DAG decomposition failed, falling back to heuristic: %s", exc)

    # Fallback heuristic decomposition (when offline / no API keys configured)
    tasks = decompose_compound_query_heuristic(active_query)
    return ExecutionPlan(
        tasks=tasks,
        original_query=query,
        rewritten_query=active_query if was_rewritten else None,
        was_rewritten=was_rewritten,
    )
