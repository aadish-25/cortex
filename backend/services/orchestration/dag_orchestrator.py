import asyncio
from dataclasses import dataclass, field
import logging
from typing import Any, Dict, List, Optional

from qdrant_client import QdrantClient
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.chat import ChatMessage, ExecutionPlan, PlanTask, PlanTaskType
from backend.models.user import User
from backend.services.orchestration.query_planner import plan_and_decompose_query
from backend.services.orchestration.sql_lookup_service import execute_sql_lookup
from backend.services.orchestration.web_search_service import WebSearchResult, execute_web_search
from backend.services.retrieval.crag_service import execute_crag_recovery
from backend.services.retrieval.reranker_service import ConfidenceLevel, ScoredChunk
from backend.services.retrieval.retrieval_service import SearchResult, internal_knowledge_search
from backend.services.retrieval.updating_policy_probe import probe_updating_policies

logger = logging.getLogger(__name__)


@dataclass
class OrchestratorResponse:
    plan: ExecutionPlan
    final_response: str
    citations: List[str] = field(default_factory=list)
    action_draft: Optional[Dict[str, Any]] = None
    confidence: ConfidenceLevel = ConfidenceLevel.HIGH


async def execute_task(
    task: PlanTask,
    client: QdrantClient,
    session: AsyncSession,
    user: User,
    scoped_upstream_context: Dict[int, Any],
) -> tuple[Any, List[str]]:
    """
    Executes a single sub-task with strict context isolation (Decision D24).
    The task ONLY sees upstream context from tasks declared in its `depends_on`.
    Returns (output, citations).
    """
    logger.info("Executing Sub-Task %d [%s]: '%s' (depends_on=%s)", task.id, task.type.value, task.query, task.depends_on)

    # 1. Internal RAG Search
    if task.type == PlanTaskType.INTERNAL_RAG:
        result: SearchResult = await internal_knowledge_search(
            client=client,
            user=user,
            query=task.query,
            top_k=5,
        )

        # Trigger Single-Pass CRAG if score is in fuzzy zone
        if result.confidence == ConfidenceLevel.FUZZY:
            result = await execute_crag_recovery(
                client=client,
                session=session,
                user=user,
                original_query=task.query,
                top_k=5,
            )
        # If low confidence, probe updating policies
        elif result.confidence == ConfidenceLevel.LOW:
            updating_notice = await probe_updating_policies(
                client=client,
                session=session,
                user=user,
                query=task.query,
            )
            if updating_notice:
                result.fallback_message = updating_notice

        citations = [c.citation for c in result.chunks if c.citation]
        return result, citations

    # 2. External Web Search
    elif task.type == PlanTaskType.EXTERNAL_WEB:
        web_results: List[WebSearchResult] = await execute_web_search(
            query=task.query,
            max_results=3,
        )
        citations = [r.url for r in web_results]
        return web_results, citations

    # 3. Direct Chat / General Chit-Chat
    elif task.type == PlanTaskType.DIRECT_CHAT:
        return "Hello! I am Cortex, your enterprise AI assistant. How can I help you today?", []

    # 4. Structured SQL Lookup (Phase 4.1 Bounded Parameterized Queries)
    elif task.type == PlanTaskType.STRUCTURED_LOOKUP:
        lookup_result = await execute_sql_lookup(
            session=session,
            user=user,
            query=task.query,
        )
        return {
            "status": "LOOKUP_RESOLVED",
            "message": lookup_result.message,
            "data": lookup_result.data,
            "intent": lookup_result.intent.value,
            "records_count": lookup_result.records_count,
        }, []

    # 5. Action Workflow (Phase 4.2 HITL ActionDraft Engine)
    elif task.type == PlanTaskType.ACTION_WORKFLOW:
        # Strictly consumes ONLY its declared upstream dependencies
        context_summary = " ".join(str(v) for v in scoped_upstream_context.values() if v)
        derived_ctx = context_summary[:200]
        try:
            from backend.services.orchestration.action_engine import action_engine
            from backend.models.action_draft import ActionDraftCreateRequest
            req = ActionDraftCreateRequest(
                query=task.query,
                domain_fields={"derived_context": derived_ctx} if derived_ctx else None,
            )
            draft_resp = await action_engine.create_draft(session, user, req)
            return {
                "status": "ACTION_DRAFT_PENDING",
                "action": task.query,
                "derived_context": derived_ctx,
                "draft": draft_resp.model_dump(),
                "draft_id": str(draft_resp.draft_id),
                "ttl_seconds_remaining": draft_resp.ttl_seconds_remaining,
                "project_key": draft_resp.project_key,
                "department": draft_resp.department,
                "message": f"Prepared official {draft_resp.department} ticket draft for '{draft_resp.title}' (Space: {draft_resp.project_key}, TTL: 15 mins).",
            }, []
        except Exception as exc:
            logger.warning("ActionEngine draft creation rejected or failed: %s", exc)
            return {
                "status": "ACTION_REFUSED",
                "action": task.query,
                "derived_context": derived_ctx,
                "message": str(exc),
            }, []

    # 6. GitHub Codebase & Issue Discovery (Phase 6.2 MCP)
    elif task.type == PlanTaskType.GITHUB_SEARCH:
        from backend.services.integrations.github_service import github_service
        q_lower = task.query.lower()
        if any(w in q_lower for w in ["pull request", "pr ", "prs"]):
            gh_result = await github_service.list_pull_requests()
            citations = [pr.get("html_url") for pr in gh_result.get("pull_requests", []) if pr.get("html_url")]
        elif any(w in q_lower for w in ["issue", "bug"]):
            gh_result = await github_service.list_issues()
            citations = [iss.get("html_url") for iss in gh_result.get("issues", []) if iss.get("html_url")]
        else:
            gh_result = await github_service.search_code(query=task.query)
            citations = [item.get("html_url") for item in gh_result.get("items", []) if item.get("html_url")]
        return gh_result, citations

    # 7. Google Drive Shared Documents Discovery (Phase 6.2 MCP)
    elif task.type == PlanTaskType.DRIVE_SEARCH:
        from backend.services.integrations.google_drive_service import google_drive_service
        drive_result = await google_drive_service.search_files(query=task.query)
        citations = [f.get("webViewLink") for f in drive_result.get("files", []) if f.get("webViewLink")]
        return drive_result, citations

    return None, []


def synthesis_join_node(
    plan: ExecutionPlan,
    task_outputs: Dict[int, Any],
    all_citations: List[str],
) -> str:
    """
    Synthesis Join Node (PRD 6.1):
    Merges resolved sub-task outputs into a cohesive markdown response.
    """
    response_sections: List[str] = []

    for task in plan.tasks:
        output = task_outputs.get(task.id)
        if not output:
            continue

        if isinstance(output, SearchResult):
            if output.fallback_message:
                response_sections.append(output.fallback_message)
            elif output.chunks:
                # Present top verified chunks
                chunk_texts = [f"- {c.text}" for c in output.chunks[:3]]
                response_sections.append("\n".join(chunk_texts))

        elif isinstance(output, list) and output and isinstance(output[0], WebSearchResult):
            web_texts = [f"- **{w.title}**: {w.snippet}" for w in output]
            response_sections.append("### External Sources\n" + "\n".join(web_texts))

        elif isinstance(output, str):
            response_sections.append(output)

        elif isinstance(output, dict):
            if output.get("status") == "ACTION_DRAFT_PENDING":
                response_sections.append(output.get("message") or f"**Action Ready for Review:** {output.get('action')}")
            elif output.get("status") == "ACTION_REFUSED":
                response_sections.append(f"**Action Notice:** {output.get('message')}")
            elif output.get("status") == "RATE_LIMITED":
                response_sections.append(f"> ⚠️ {output.get('message')}")
            elif output.get("items"):  # GitHub code search
                items = [f"- [`{item['name']}`]({item['html_url']}): {item.get('path')}" for item in output["items"]]
                response_sections.append(f"### GitHub Code Search ({output.get('query')})\n" + "\n".join(items))
            elif output.get("pull_requests"):  # GitHub PRs
                prs = [f"- [PR #{pr['number']}: {pr['title']}]({pr['html_url']}) ({pr['state']}, by @{pr['author']})" for pr in output["pull_requests"]]
                response_sections.append("### GitHub Pull Requests\n" + "\n".join(prs))
            elif output.get("issues"):  # GitHub Issues
                issues = [f"- [Issue #{iss['number']}: {iss['title']}]({iss['html_url']}) ({iss['state']}, by @{iss['author']})" for iss in output["issues"]]
                response_sections.append("### GitHub Issues\n" + "\n".join(issues))
            elif output.get("files"):  # Google Drive search
                files = [f"- [{f['name']}]({f.get('webViewLink', '#')}) (Type: {f.get('mimeType', 'unknown')})" for f in output["files"]]
                response_sections.append(f"### Google Drive Search ({output.get('query')})\n" + "\n".join(files))
            elif output.get("content"):  # File content read
                response_sections.append(f"### Document Content: {output.get('name') or output.get('id')}\n```\n{output['content'][:1000]}\n```")
            else:
                response_sections.append(output.get("message", ""))

    return "\n\n".join(response_sections)


async def orchestrate_dag_execution(
    client: QdrantClient,
    session: AsyncSession,
    user: User,
    query: str,
    history: Optional[List[ChatMessage]] = None,
) -> OrchestratorResponse:
    """
    Master DAG Execution Engine (Decision D24 & D26):
    1. Query Planner: Rewriting + Decomposition into DAG tasks.
    2. Topological Concurrency: Independent tasks run in parallel via asyncio.gather.
    3. Strict Context Isolation: Dependent tasks receive only declared upstream context.
    4. Synthesis Join: Assembles final markdown response with unified citations.
    """
    # Step 1: Generate Execution Plan
    plan = await plan_and_decompose_query(query=query, history=history)

    task_outputs: Dict[int, Any] = {}
    all_citations: List[str] = []

    # Step 2: Separate independent and dependent tasks
    independent_tasks = [t for t in plan.tasks if not t.depends_on]
    dependent_tasks = [t for t in plan.tasks if t.depends_on]

    # Step 3: Execute independent tasks concurrently via asyncio.gather()
    if independent_tasks:
        async def run_independent(t: PlanTask):
            out, cites = await execute_task(t, client, session, user, scoped_upstream_context={})
            t.status = "COMPLETED"
            t.output = out
            t.citations = cites
            return t.id, out, cites

        results = await asyncio.gather(*[run_independent(t) for t in independent_tasks])
        for tid, out, cites in results:
            task_outputs[tid] = out
            all_citations.extend(cites)

    # Step 4: Execute dependent tasks in topological sequence
    for task in dependent_tasks:
        # Strict Context Scoping (Zero Pollution): Extract ONLY declared dependencies
        scoped_upstream = {
            dep_id: task_outputs.get(dep_id)
            for dep_id in task.depends_on
            if dep_id in task_outputs
        }
        out, cites = await execute_task(task, client, session, user, scoped_upstream)
        task.status = "COMPLETED"
        task.output = out
        task.citations = cites
        task_outputs[task.id] = out
        all_citations.extend(cites)

    # Step 5: Synthesis Join Node
    final_text = synthesis_join_node(plan, task_outputs, all_citations)

    # Determine overall confidence and extract action draft if present
    overall_confidence = ConfidenceLevel.HIGH
    action_draft_payload = None
    for out in task_outputs.values():
        if isinstance(out, SearchResult) and out.confidence == ConfidenceLevel.LOW:
            overall_confidence = ConfidenceLevel.LOW
        elif isinstance(out, dict) and out.get("status") == "ACTION_DRAFT_PENDING" and out.get("draft"):
            action_draft_payload = out["draft"]

    # Deduplicate citations
    deduped_citations = list(dict.fromkeys(all_citations))

    return OrchestratorResponse(
        plan=plan,
        final_response=final_text,
        citations=deduped_citations,
        action_draft=action_draft_payload,
        confidence=overall_confidence,
    )
