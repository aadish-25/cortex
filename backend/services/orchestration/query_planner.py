import json
import logging
import re
from typing import List, Optional

from pydantic import BaseModel, Field

from backend.core.config import settings
from backend.models.chat import ChatMessage, ChatRole, ExecutionPlan, PlanTask, PlanTaskType, ToolAction
from backend.services.orchestration.llm_service import llm_service
from backend.services.orchestration.memory_service import estimate_token_count

logger = logging.getLogger(__name__)

# Cap planner (Call 1) history context at 3,000 tokens so fast routing stays low-latency
# while Synthesis (Call 2) receives the full 8,000-token window.
PLANNER_HISTORY_TOKEN_BUDGET = 3000

# Heuristic patterns identifying pronouns and relational anaphora in conversational follow-ups (Fallback only)
ANAPHORA_PATTERN = re.compile(
    r"\b(it|its|they|them|their|those|these|this|that|he|she|his|her)\b|"
    r"\b(what about|how about|can i claim that|the policy|the rule|the same|same for|as mentioned|above|previous|open it|add to memor|remember)\b|"
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
        description="Task intent type: INTERNAL_RAG, EXTERNAL_WEB, STRUCTURED_LOOKUP, ACTION_WORKFLOW, GITHUB_SEARCH, DRIVE_SEARCH, ADMIN_OPERATION, DIRECT_CHAT"
    )
    query: str = Field(description="Clean, typo-corrected search query, filename, or instruction for this specific task")
    tool_action: Optional[ToolAction] = Field(
        default=None,
        description=(
            "Sub-action for tool tasks: "
            "DRIVE_SEARCH -> 'drive_list' | 'drive_search' | 'drive_read_file'; "
            "GITHUB_SEARCH -> 'github_list_issues' | 'github_issues_with_files' | 'github_list_prs' | 'github_search_code' | 'github_read_file'; "
            "ADMIN_OPERATION -> 'admin_health' | 'admin_audit_logs' | 'admin_doc_status' | 'admin_reindex'. "
            "Null for other task types."
        ),
    )
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
    max_turns: Optional[int] = None,
    summary: Optional[str] = None,
    max_tokens: Optional[int] = None,
) -> str:
    """
    Formats conversation history for the LLM.
    - Preserves any SYSTEM summary message injected by prepare_context_window (or explicit `summary`).
    - When `max_turns` is set (e.g., query rewrite), keeps at most the last `max_turns * 2` dialogue messages.
    - When `max_tokens` is set (e.g., Planner Call 1 budget), includes summary + as many newest
      dialogue messages as fit within `max_tokens`.
    """
    parts: List[str] = []

    if not history:
        if summary:
            parts.append(f"[Prior Conversation Summary]: {summary}")
        return "\n".join(parts)

    system_msgs = [m for m in history if m.role == ChatRole.SYSTEM]
    dialogue = [m for m in history if m.role in (ChatRole.USER, ChatRole.ASSISTANT)]

    if max_turns is not None:
        dialogue = dialogue[-(max_turns * 2):]

    summary_block = ""
    if system_msgs:
        summary_block = system_msgs[-1].content
    elif summary:
        summary_block = f"[Prior Conversation Summary]: {summary}"

    if summary_block:
        parts.append(summary_block)

    if max_tokens is not None:
        remaining = max(0, max_tokens - estimate_token_count(summary_block))
        selected_lines: List[str] = []
        used = 0
        for m in reversed(dialogue):
            line = f"{m.role.value.capitalize()}: {m.content}"
            t = estimate_token_count(line)
            if selected_lines and (used + t > remaining):
                break
            selected_lines.insert(0, line)
            used += t
        parts.extend(selected_lines)
    else:
        for m in dialogue:
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

    if any(k in q_lower for k in ["add to memor", "save to memor", "remember that", "remember this", "i prefer ", "i like "]) and not any(k in q_lower for k in ["policy", "leave", "ticket", "github", "drive"]):
        return PlanTaskType.DIRECT_CHAT
    if any(k in q_lower for k in ["leave balance", "my tickets", "ticket status", "department head", "manager for"]):
        return PlanTaskType.STRUCTURED_LOOKUP
    elif any(k in q_lower for k in ["draft", "raise ticket", "create ticket", "file complaint", "apply for leave", "submit dispute"]):
        return PlanTaskType.ACTION_WORKFLOW
    elif any(k in q_lower for k in ["github", "pull request", "repo", "pr #", "git commit", "codebase", "branch"]):
        return PlanTaskType.GITHUB_SEARCH
    elif any(k in q_lower for k in ["google drive", "shared drive", "gdoc", "drive file", "google doc", "my drive", "in drive"]) or re.search(r"\.(docx|pdf|png|jpg|jpeg|gdoc)\b", q_lower):
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

    # Step 1: Turn-based query rewrite (use last 2 turns only — rewrite doesn't need full history)
    active_query, was_rewritten = await rewrite_query(query, history, summary=summary)
    # Planner (Call 1) receives [Summary if exists] + newest unsummarized turns up to 3,000 tokens
    recent_history_block = format_conversation_history(
        history,
        summary=summary,
        max_tokens=PLANNER_HISTORY_TOKEN_BUDGET,
    )

    # Step 2 (PRIMARY PATH): Fast LLM Structured Planning (routing + sub-actions + memory detection in ONE call)
    if llm_service.is_configured():
        history_section = (
            f"Recent Conversation Context (use to resolve follow-ups like 'open it', bare filenames, or 'add to memories'):\n{recent_history_block}\n\n"
            if recent_history_block
            else ""
        )
        planner_instructions = (
            "You are Cortex's query planner. Understand the user's true intent across English, Hinglish, slang, and typos, "
            "using Recent Conversation Context to resolve references and infer which source/tool a follow-up belongs to.\n\n"
            "Tools & sub-actions:\n"
            "- INTERNAL_RAG: Company policies, handbooks, SOPs, compliance docs. Normalize `query` into a clean, spell-corrected search phrase.\n"
            "- EXTERNAL_WEB: Public internet data (market rates, news, external benchmarks).\n"
            "- STRUCTURED_LOOKUP: User's own SQL records (leave balance, their Jira tickets, department heads, profile).\n"
            "- ACTION_WORKFLOW: Raising formal workplace Jira tickets/requests (IT, HR, Finance) ONLY — never for saving user memories/personalization.\n"
            "- GITHUB_SEARCH: Repository code, issues, PRs, or opening a repo file. Set `tool_action` to `github_list_issues`, `github_issues_with_files`, `github_list_prs`, `github_search_code`, or `github_read_file` (with file path in `query`).\n"
            "- DRIVE_SEARCH: Google Drive files. Set `tool_action` to `drive_list` (`query=\"\"`), `drive_search`, or `drive_read_file` (exact file name/keyword in `query`).\n"
            "- ADMIN_OPERATION (Admin role only): `admin_health`, `admin_audit_logs`, `admin_doc_status`, or `admin_reindex`.\n"
            "- DIRECT_CHAT: Greetings, general conversation, assistant capability questions, or personal advice/questions not requiring enterprise tools.\n\n"
            f"Authenticated role: {user_role or 'Unknown'}.\n"
            "Principles:\n"
            "1. Context-first tool selection: Never hard-assume a file request belongs to Drive — use Recent Conversation Context and cues to pick Drive vs GitHub vs INTERNAL_RAG.\n"
            "2. Decompose multi-intent prompts into minimal tasks with `depends_on` only where an action requires upstream output.\n\n"
        )
        prompt = f"{planner_instructions}{history_section}User Prompt: {active_query}\n"

        # Exact tiktoken telemetry for Call 1 (Planner)
        planner_sys_tokens = estimate_token_count(planner_instructions)
        planner_schema_tokens = estimate_token_count(json.dumps(LLMExecutionPlan.model_json_schema()))
        planner_hist_tokens = estimate_token_count(history_section)
        planner_query_tokens = estimate_token_count(active_query)
        logger.info(
            "[TokenTelemetry][Planner] system_instructions=%d schema=%d history=%d query=%d total_input=%d (model=%s)",
            planner_sys_tokens,
            planner_schema_tokens,
            planner_hist_tokens,
            planner_query_tokens,
            planner_sys_tokens + planner_schema_tokens + planner_hist_tokens + planner_query_tokens,
            settings.FAST_ROUTING_MODEL,
        )
        try:
            parsed = await llm_service.generate_structured(
                prompt=prompt,
                response_model=LLMExecutionPlan,
                model=settings.FAST_ROUTING_MODEL,
            )
            if parsed and parsed.tasks:
                parsed_tasks: List[PlanTask] = []
                for t in parsed.tasks:
                    # Enforce RBAC fail-closed: non-admins can never execute ADMIN_OPERATION
                    task_type = t.type
                    tool_action = t.tool_action
                    if task_type == PlanTaskType.ADMIN_OPERATION and user_role != "Admin":
                        task_type = PlanTaskType.INTERNAL_RAG
                        tool_action = None
                    parsed_tasks.append(
                        PlanTask(
                            id=t.id,
                            type=task_type,
                            query=t.query,
                            tool_action=tool_action,
                            depends_on=t.depends_on,
                        )
                    )
                return ExecutionPlan(
                    tasks=parsed_tasks,
                    original_query=query,
                    rewritten_query=active_query if was_rewritten else None,
                    was_rewritten=was_rewritten,
                )
        except Exception as exc:
            logger.warning("LLM DAG decomposition failed, falling back to offline heuristic: %s", exc)

    # Step 3 (OFFLINE / ERROR FALLBACK ONLY): Heuristic routing when LLM is unavailable
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

    tasks = decompose_compound_query_heuristic(active_query)
    return ExecutionPlan(
        tasks=tasks,
        original_query=query,
        rewritten_query=active_query if was_rewritten else None,
        was_rewritten=was_rewritten,
    )
