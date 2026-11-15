import json
import logging
import re
from typing import List, Optional

from backend.core.config import settings
from backend.models.chat import ChatMessage, ChatRole, ExecutionPlan, PlanTask, PlanTaskType

logger = logging.getLogger(__name__)

# Heuristic patterns identifying pronouns and relational anaphora in conversational follow-ups
ANAPHORA_PATTERN = re.compile(
    r"\b(it|its|they|them|their|those|these|this|that|he|she|his|her)\b|"
    r"\b(what about|how about|can i claim that|the policy|the rule|the same|same for|as mentioned|above|previous)\b|"
    r"\.\.\.$",
    re.IGNORECASE,
)


def detect_anaphora_or_pronoun(query: str) -> bool:
    """
    Heuristic check for relational references and pronouns.
    Returns True if the query appears to be an ambiguous conversational follow-up.
    """
    return bool(ANAPHORA_PATTERN.search(query.strip()))


def extract_recent_history_window(history: List[ChatMessage], max_messages: int = 4) -> List[ChatMessage]:
    """
    Extracts static sliding window of the last 2 conversation turns (max 4 messages).
    Excludes system messages to avoid context bloat.
    """
    non_system = [m for m in history if m.role in (ChatRole.USER, ChatRole.ASSISTANT)]
    return non_system[-max_messages:]


async def rewrite_query(
    query: str,
    history: List[ChatMessage],
) -> tuple[str, bool]:
    """
    Conversational Query Rewriting Node (PRD 6.2):
    1. Turn 1 (history empty): 0ms Zero-Latency Bypass.
    2. Turn 2+ (no pronouns/anaphora): 0ms Zero-Latency Bypass.
    3. Turn 2+ (anaphora detected): Resolves context using last 2 turns (max 4 messages).
    Returns (resolved_query, was_rewritten).
    """
    # 1. Turn 1 Zero-Latency Bypass (0ms overhead)
    if not history:
        return query, False

    # 2. Check for anaphora / pronouns
    if not detect_anaphora_or_pronoun(query):
        return query, False

    recent_turns = extract_recent_history_window(history, max_messages=4)
    if not recent_turns:
        return query, False

    # 3. LLM-driven query rewriting if Gemini API key is configured
    if settings.GEMINI_API_KEY:
        try:
            from google import genai
            client = genai.Client(api_key=settings.GEMINI_API_KEY)
            
            conversation_context = "\n".join(
                f"{m.role.value.capitalize()}: {m.content}" for m in recent_turns
            )
            prompt = (
                "You are an expert conversational query rewriter. Given the conversation history, "
                "rewrite the user's follow-up question into a single self-contained, standalone question "
                "that can be searched in an enterprise vector database without ambiguity.\n"
                "Strip pronouns like 'it', 'that', 'those' and replace them with their specific referents.\n"
                "Return ONLY the rewritten query text and nothing else.\n\n"
                f"Conversation History:\n{conversation_context}\n\n"
                f"User Follow-up Question: {query}\n"
                "Rewritten Query:"
            )
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
            )
            rewritten = (response.text or "").strip()
            if rewritten:
                return rewritten, True
        except Exception as exc:
            logger.warning("Gemini query rewriting failed, falling back to heuristic fusion: %s", exc)

    # 4. Deterministic heuristic fallback when offline or no API key
    # Finds the last user query to supply the missing subject
    last_user_msg = next((m for m in reversed(recent_turns) if m.role == ChatRole.USER), None)
    if last_user_msg:
        # Combine the context of the previous question with the follow-up
        heuristic_query = f"{query} (in context of: {last_user_msg.content})"
        return heuristic_query, True

    return query, False


def classify_task_intent(query_text: str) -> PlanTaskType:
    """Classifies task intent using deterministic enterprise heuristics."""
    q_lower = query_text.lower()

    if any(k in q_lower for k in ["leave balance", "my tickets", "ticket status", "department head", "manager for"]):
        return PlanTaskType.STRUCTURED_LOOKUP
    elif any(k in q_lower for k in ["draft", "raise ticket", "create ticket", "file complaint", "apply for leave", "submit dispute"]):
        return PlanTaskType.ACTION_WORKFLOW
    elif any(k in q_lower for k in ["external", "market price", "latest 2026", "web search", "google", "flight benchmark", "news"]):
        return PlanTaskType.EXTERNAL_WEB
    elif any(k in q_lower for k in ["hi", "hello", "hey", "who are you", "what can you do", "thanks", "thank you"]) and len(query_text.split()) < 5:
        return PlanTaskType.DIRECT_CHAT

    return PlanTaskType.INTERNAL_RAG


def decompose_compound_query_heuristic(query: str) -> List[PlanTask]:
    """
    Deterministic rule-based decomposition for compound prompts (Decision D24 fallback).
    Detects conjunctions ('and also', 'then', 'as well as', 'after that') and assigns dependencies.
    """
    # Split on explicit multi-command conjunctions
    split_pattern = re.compile(r"\s*(?:;\s*|\band then\b|\band also\b|\bafter that\b|\bas well as\b)\s*", re.IGNORECASE)
    parts = [p.strip() for p in split_pattern.split(query) if p.strip()]

    # If simple single-clause prompt, return single task
    if len(parts) <= 1:
        task_type = classify_task_intent(query)
        return [PlanTask(id=1, type=task_type, query=query, depends_on=[])]

    tasks: List[PlanTask] = []
    lookup_and_rag_ids = []

    for idx, part in enumerate(parts, start=1):
        task_type = classify_task_intent(part)

        # Action workflows (e.g. draft ticket) depend on earlier retrieval/lookups
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
) -> ExecutionPlan:
    """
    Master Query Planner (Phase 3.1 & Decision D24):
    1. Rewrites conversational query if Turn 2+ with anaphora.
    2. Decomposes compound prompt into a DAG of sub-tasks with strict depends_on IDs.
    """
    history = history or []

    # Step 1: Turn-based query rewrite
    active_query, was_rewritten = await rewrite_query(query, history)

    # Step 2: Multi-Query Decomposition
    if settings.GEMINI_API_KEY:
        try:
            from google import genai
            client = genai.Client(api_key=settings.GEMINI_API_KEY)
            
            prompt = (
                "You are an enterprise query planner DAG engine. Decompose the user's prompt into an array "
                "of discrete sub-tasks. Each sub-task must have an integer id (1, 2, ...), a type, "
                "a standalone query, and an array of depends_on ids.\n\n"
                "Allowed task types:\n"
                "- INTERNAL_RAG: querying company policies, technical offsites, per diems, security.\n"
                "- EXTERNAL_WEB: searching public internet for external benchmarks, tax laws, vendor info.\n"
                "- STRUCTURED_LOOKUP: querying user-specific DB rows (leave balances, ticket status).\n"
                "- ACTION_WORKFLOW: creating or drafting Jira tickets, requesting equipment or leave.\n"
                "- DIRECT_CHAT: general greetings, polite exchanges.\n\n"
                "Rules:\n"
                "- Independent tasks must have depends_on: [].\n"
                "- Drafting or action tasks that need info from lookup or RAG must declare those task ids in depends_on.\n"
                "- Return STRICTLY valid JSON with no markdown formatting.\n\n"
                f"User Prompt: {active_query}\n"
                "JSON:"
            )
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
            )
            cleaned_text = (response.text or "").strip()
            if cleaned_text.startswith("```"):
                cleaned_text = re.sub(r"^```(?:json)?\n|\n```$", "", cleaned_text)
            
            task_dicts = json.loads(cleaned_text)
            parsed_tasks = [
                PlanTask(
                    id=t["id"],
                    type=PlanTaskType(t["type"]),
                    query=t["query"],
                    depends_on=t.get("depends_on", []),
                )
                for t in task_dicts
            ]
            if parsed_tasks:
                return ExecutionPlan(
                    tasks=parsed_tasks,
                    original_query=query,
                    rewritten_query=active_query if was_rewritten else None,
                    was_rewritten=was_rewritten,
                )
        except Exception as exc:
            logger.warning("LLM DAG decomposition failed, falling back to heuristic: %s", exc)

    # Fallback heuristic decomposition
    tasks = decompose_compound_query_heuristic(active_query)
    return ExecutionPlan(
        tasks=tasks,
        original_query=query,
        rewritten_query=active_query if was_rewritten else None,
        was_rewritten=was_rewritten,
    )
