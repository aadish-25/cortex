import asyncio
from dataclasses import dataclass, field
import logging
import re
from typing import Any, Dict, List, Optional

from qdrant_client import QdrantClient
from sqlalchemy.ext.asyncio import AsyncSession
from backend.db.database import async_session

from backend.models.chat import ChatMessage, ExecutionPlan, PlanTask, PlanTaskType, ToolAction
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
    citation_map: Dict[int, str] = field(default_factory=dict)  # F4: {1: "source string", ...}
    evidence_chunks: List[Dict[str, Any]] = field(default_factory=list)  # F8: [{anchor, chunk_id, citation, section}]
    action_draft: Optional[Dict[str, Any]] = None
    confidence: ConfidenceLevel = ConfidenceLevel.HIGH
    reranker_scores: List[float] = field(default_factory=list)
    top_reranker_score: Optional[float] = None


async def execute_task(
    task: PlanTask,
    client: QdrantClient,
    session: AsyncSession,
    user: User,
    scoped_upstream_context: Dict[int, Any],
    retrieval_top_k: int = 5,
    history: Optional[List[ChatMessage]] = None,
) -> tuple[Any, List[str]]:
    """
    Executes a single sub-task with strict context isolation (Decision D24).
    The task ONLY sees upstream context from tasks declared in its `depends_on`.
    Returns (output, citations).
    """
    logger.info("Executing Sub-Task %d [%s] (depends_on=%s)", task.id, task.type.value, task.depends_on)

    # 1. Internal RAG Search
    if task.type == PlanTaskType.INTERNAL_RAG:
        result: SearchResult = await internal_knowledge_search(
            client=client,
            user=user,
            query=task.query,
            top_k=retrieval_top_k,
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
        # If low confidence, try LLM-first CRAG recovery (which also runs updating policy probe on miss)
        elif result.confidence == ConfidenceLevel.LOW:
            from backend.services.orchestration.llm_service import llm_service
            from backend.services.retrieval.crag_service import reformulate_query_for_crag
            if llm_service.is_configured() or reformulate_query_for_crag(task.query).lower() != task.query.strip().lower():
                result = await execute_crag_recovery(
                    client=client,
                    session=session,
                    user=user,
                    original_query=task.query,
                    top_k=5,
                )
            else:
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
            user_id=user.id,
            session=session,
        )
        # F2 Fix: empty list means search was unavailable — return structured unavailable dict
        if not web_results:
            return {
                "status": "SEARCH_UNAVAILABLE",
                "query": task.query,
                "message": "External search is currently unavailable. No external sources were consulted.",
            }, []
        citations = [r.url for r in web_results]
        return web_results, citations

    # 3. Direct Chat / General Chit-Chat / Assistant Capabilities / Personal Preferences
    elif task.type == PlanTaskType.DIRECT_CHAT:
        return (
            "DIRECT_CHAT_CONTEXT:\n"
            "You are Cortex, an enterprise AI workspace assistant.\n"
            "Your live capabilities:\n"
            "- Search and cite internal company documents scoped to the user's department & clearance.\n"
            "- List, search, and read text from Google Drive files (Google Docs, PDF, DOCX, Markdown, TXT). "
            "Note: You cannot visually open or inspect image files (PNG/JPG/etc.), though you can provide their Drive link.\n"
            "- Inspect GitHub issues, pull requests, and repository code.\n"
            "- Look up structured HR/leave/ticket records and draft Jira workflow tickets.\n"
            "- Remember persistent personal preferences and facts in the user's Memories.\n"
            "Respond naturally, directly, and helpfully to the user's message."
        ), []

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
                "draft": draft_resp.model_dump(mode="json"),
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
                "message": f"Ticketing service is temporarily unavailable ({str(exc)}). Please retry in a few moments.",
            }, []

    # 6. GitHub Codebase & Issue Discovery (Phase 6.2 MCP)
    # PRIMARY: Dispatch on LLM-emitted `task.tool_action`.
    # FALLBACK (only when tool_action is None, e.g. offline/unit test): keyword heuristic.
    elif task.type == PlanTaskType.GITHUB_SEARCH:
        from backend.services.integrations.github_service import github_service
        action = task.tool_action
        if action is None:
            q_lower = task.query.lower()
            if any(w in q_lower for w in ["pull request", "pr ", "prs"]):
                action = ToolAction.GITHUB_LIST_PRS
            elif any(w in q_lower for w in ["issue", "issues", "bug", "bugs"]):
                action = (
                    ToolAction.GITHUB_ISSUES_WITH_FILES
                    if any(w in q_lower for w in ["file", "code", "correspond", "where", "path", "module"])
                    else ToolAction.GITHUB_LIST_ISSUES
                )
            else:
                action = ToolAction.GITHUB_SEARCH_CODE

        if action == ToolAction.GITHUB_LIST_PRS:
            gh_result = await github_service.list_pull_requests()
            citations = [pr.get("html_url") for pr in gh_result.get("pull_requests", []) if pr.get("html_url")]
        elif action in (ToolAction.GITHUB_LIST_ISSUES, ToolAction.GITHUB_ISSUES_WITH_FILES):
            gh_result = await github_service.list_issues(per_page=10)
            citations = [iss.get("html_url") for iss in gh_result.get("issues", []) if iss.get("html_url")]
            if action == ToolAction.GITHUB_ISSUES_WITH_FILES:
                repo_files = await github_service.list_repo_files()
                if repo_files:
                    gh_result["repo_files"] = repo_files
        elif action == ToolAction.GITHUB_READ_FILE:
            raw_path = task.query.strip()
            # Extract clean file path if query contains surrounding natural language
            # e.g., "security vulnerabilities in backend/auth_service.py" -> "backend/auth_service.py"
            path_match = re.search(
                r"((?:[\w\-.]+/)*[\w\-.]+\.(?:py|ts|js|jsx|tsx|json|go|rs|md|txt|sql|html|css|yaml|yml|sh|env))",
                raw_path,
                re.IGNORECASE,
            )
            clean_path = path_match.group(1) if path_match else raw_path

            # If no clean path was found or path lacks file extension/slash, search conversation history for recently inspected repo files
            if ("." not in clean_path or "/" not in clean_path) and history:
                recent_history_text = " ".join(m.content for m in history[-4:])
                hist_matches = re.findall(
                    r"((?:[\w\-.]+/)+[\w\-.]+\.(?:py|ts|js|jsx|tsx|json|go|rs|md|txt|sql|html|css|yaml|yml|sh|env))",
                    recent_history_text,
                    re.IGNORECASE,
                )
                if hist_matches:
                    clean_path = sorted(hist_matches, key=lambda p: ("/" in p, len(p)), reverse=True)[0]

            gh_result = await github_service.get_file_contents(path=clean_path)
            citations = [gh_result["html_url"]] if gh_result.get("html_url") else []
        else:
            gh_result = await github_service.search_code(query=task.query)
            citations = [item.get("html_url") for item in gh_result.get("items", []) if item.get("html_url")]
        return gh_result, citations

    # 7. Privileged administrative operations (strictly Admin-only)
    elif task.type == PlanTaskType.ADMIN_OPERATION:
        from backend.services.admin.admin_agent import admin_agent
        return await admin_agent.execute_from_query(
            session=session,
            user=user,
            query=task.query,
            tool_action=task.tool_action,
        ), []

    # 8. Google Drive Shared Documents Discovery & Content Extraction (Phase 6.2 MCP)
    # PRIMARY: Dispatch on LLM-emitted `task.tool_action` ('drive_list', 'drive_search', 'drive_read_file').
    # FALLBACK (only when tool_action is None, e.g. offline/unit test): regex/keyword heuristic.
    elif task.type == PlanTaskType.DRIVE_SEARCH:
        from backend.services.integrations.google_drive_service import google_drive_service
        q_raw = (task.query or "").strip()
        action = task.tool_action

        if action is None:
            q_lower = q_raw.lower()
            if not q_raw:
                action = ToolAction.DRIVE_LIST
            elif re.search(r"\.(docx|pdf|md|txt|gdoc|png|jpg|jpeg|webp|csv)\b", q_lower) or any(
                w in q_lower for w in ["open", "read", "tell me more", "summarize", "summary", "explain", "content", "inside", "details", "about "]
            ):
                action = ToolAction.DRIVE_READ_FILE
            else:
                action = ToolAction.DRIVE_SEARCH

        if action == ToolAction.DRIVE_LIST:
            drive_result = await google_drive_service.search_files(query="")
            files = drive_result.get("files") or []
            citations = [f.get("webViewLink") for f in files if f.get("webViewLink")]
            return drive_result, citations

        # For drive_search / drive_read_file, extract exact filename token if present in query
        file_match = re.search(r"([A-Za-z0-9_\-\. ]+\.(?:docx|pdf|md|txt|gdoc|png|jpg|jpeg|webp|csv))", q_raw, re.IGNORECASE)
        search_term = file_match.group(1).strip() if file_match else q_raw

        drive_result = await google_drive_service.search_files(query=search_term)
        files = drive_result.get("files") or []

        # If exact name search returned nothing, list folder and match against available filenames
        if not files and search_term:
            all_res = await google_drive_service.search_files(query="")
            all_files = all_res.get("files") or []
            tokens = [t for t in re.split(r"[\s_\-\.]+", search_term.lower()) if len(t) > 2]
            matched = [
                f for f in all_files
                if any(t in (f.get("name") or "").lower() for t in tokens)
            ] if tokens else []
            if matched:
                drive_result = {**all_res, "files": matched, "total_count": len(matched)}
                files = matched
            else:
                drive_result = {**drive_result, "available_files": [f.get("name") for f in all_files]}

        if action == ToolAction.DRIVE_READ_FILE and files:
            target_file = files[0]
            file_id = target_file.get("id")
            if file_id:
                read_res = await google_drive_service.read_file_text(file_id)
                drive_result["opened_file"] = read_res

        citations = [f.get("webViewLink") for f in files if f.get("webViewLink")]
        return drive_result, citations

    return None, []


def synthesis_join_node(
    plan: ExecutionPlan,
    task_outputs: Dict[int, Any],
    all_citations: List[str],
) -> tuple[str, Dict[int, str], List[Dict[str, Any]]]:
    """
    Synthesis Join Node (PRD 6.1):
    Merges resolved sub-task outputs into a cohesive markdown response.

    F4 Fix: Returns a (response_text, citation_map, evidence_chunks) tuple where
    citation_map maps numeric anchors {1: "citation_string", ...} and evidence_chunks
    contains exact chunk_ids for the 3-chunk evidence drawer (PRD 10.3).
    """
    response_sections: List[str] = []
    citation_map: Dict[int, str] = {}  # {anchor_num: citation_string}
    evidence_chunks: List[Dict[str, Any]] = []
    anchor_counter = 0

    for task in plan.tasks:
        output = task_outputs.get(task.id)
        if not output:
            continue

        if isinstance(output, SearchResult):
            if output.fallback_message:
                response_sections.append(output.fallback_message)
            elif output.chunks:
                # F4 Fix: Number each chunk and build citation map
                # F8 Fix: Retain chunk_id for evidence drawer context
                numbered_chunks = []
                for chunk in output.chunks[:5]:
                    anchor_counter += 1
                    citation_map[anchor_counter] = chunk.citation
                    evidence_chunks.append({
                        "anchor": anchor_counter,
                        "chunk_id": getattr(chunk, "chunk_id", ""),
                        "citation": chunk.citation,
                        "section": getattr(chunk, "section", ""),
                        "department": getattr(chunk, "department", ""),
                    })
                    numbered_chunks.append(
                        f"[{anchor_counter}] [{chunk.citation}]\n{chunk.text}"
                    )
                citation_index = "\n".join(
                    f"[{n}] {c}" for n, c in citation_map.items()
                )
                chunk_block = "\n\n".join(numbered_chunks)
                response_sections.append(
                    f"{chunk_block}\n\nCitation Map:\n{citation_index}"
                )

        elif isinstance(output, list) and output and isinstance(output[0], WebSearchResult):
            # F3 Fix: Use sandboxed_xml (Decision D25 isolation) and give external sources stable anchors.
            xml_blocks = "\n".join(w.sandboxed_xml for w in output)
            external_index = []
            for source in output:
                anchor_counter += 1
                citation_map[anchor_counter] = f"{source.title} — {source.url}"
                external_index.append(f"[{anchor_counter}] {source.title} — {source.url}")
            response_sections.append(
                f"<external_web_sources>\n{xml_blocks}\n</external_web_sources>\n"
                + "External citation map:\n" + "\n".join(external_index)
            )

        elif isinstance(output, str):
            response_sections.append(output)

        elif isinstance(output, dict):
            if output.get("status") == "ACTION_DRAFT_PENDING":
                response_sections.append(output.get("message") or f"**Action Ready for Review:** {output.get('action')}")
            elif output.get("status") == "ACTION_REFUSED":
                response_sections.append(f"**Action Notice:** {output.get('message')}")
            elif output.get("status") == "RATE_LIMITED":
                response_sections.append(f"> {output.get('message')}")
            elif output.get("status") == "SEARCH_UNAVAILABLE":
                # F2 Fix: Search unavailable — do not fabricate sources
                response_sections.append(f"> {output.get('message')}")
            elif output.get("status") == "ADMIN_TOOL_RESULT":
                response_sections.append(output.get("message", "Administrative operation completed."))
            elif output.get("items"):  # GitHub code search
                items = [f"- [`{item['name']}`]({item['html_url']}): {item.get('path')}" for item in output["items"]]
                response_sections.append(f"### GitHub Code Search ({output.get('query')})\n" + "\n".join(items))
            elif "items" in output and output.get("status") == "SUCCESS":
                response_sections.append(f"### GitHub Code Search ({output.get('query')})\nNo matching code files were found in the repository.")
            elif output.get("pull_requests"):  # GitHub PRs
                prs = [f"- [PR #{pr['number']}: {pr['title']}]({pr['html_url']}) ({pr['state']}, by @{pr['author']})" for pr in output["pull_requests"]]
                response_sections.append("### GitHub Pull Requests\n" + "\n".join(prs))
            elif output.get("issues"):  # GitHub Issues
                issue_lines = []
                for iss in output["issues"]:
                    line = f"- [Issue #{iss['number']}: {iss['title']}]({iss['html_url']}) ({iss['state']}, by @{iss['author']})"
                    if iss.get("body"):
                        line += f"\n  Description: {iss['body']}"
                    issue_lines.append(line)
                section = "### GitHub Issues\n" + "\n".join(issue_lines)
                if output.get("repo_files"):
                    section += "\n\n### Repository Files\n" + "\n".join(f"- `{p}`" for p in output["repo_files"])
                response_sections.append(section)
            elif "issues" in output and output.get("status") == "SUCCESS":
                response_sections.append("### GitHub Issues\nThere are currently no open issues in the connected repository.")
            elif output.get("files"):  # Google Drive search (with optional opened content)
                files = [f"- [{f['name']}]({f.get('webViewLink', '#')}) (Type: {f.get('mimeType', 'unknown')})" for f in output["files"]]
                section = f"### Google Drive Search ({output.get('query')})\n" + "\n".join(files)
                opened = output.get("opened_file")
                if isinstance(opened, dict):
                    if opened.get("content"):
                        section += f"\n\n### Extracted Document Content: {opened.get('name') or opened.get('id')}\n{opened['content'][:6000]}"
                    elif opened.get("message"):
                        section += f"\n\n### File Access Notice\n{opened['message']}"
                response_sections.append(section)
            elif "files" in output and output.get("status") == "SUCCESS":  # Google Drive search returned 0 files
                avail = output.get("available_files") or []
                avail_note = ("\nAvailable files in Drive:\n" + "\n".join(f"- {n}" for n in avail)) if avail else ""
                response_sections.append(
                    f"### Google Drive Search ({output.get('query')})\n"
                    f"No file named '{output.get('query')}' was found in the connected Google Drive folder.{avail_note}"
                )
            elif output.get("content"):  # File content read
                file_label = output.get("path") or output.get("name") or output.get("id") or "File"
                response_sections.append(f"### Document Content: {file_label}\n```\n{output['content'][:6000]}\n```")
            elif output.get("status") in ("ERROR", "RATE_LIMITED"):
                response_sections.append(f"### Tool Execution Notice\n{output.get('message', 'Service notice occurred.')}")
            else:
                response_sections.append(output.get("message", ""))

    return "\n\n".join(response_sections), citation_map, evidence_chunks


def _topological_layers(tasks: List[PlanTask]) -> List[List[PlanTask]]:
    """Validate task IDs/dependencies and return deterministic topological layers."""
    by_id = {task.id: task for task in tasks}
    if len(by_id) != len(tasks):
        raise ValueError("Execution plan contains duplicate task IDs")
    for task in tasks:
        if task.id in task.depends_on:
            raise ValueError(f"Task {task.id} cannot depend on itself")
        missing = set(task.depends_on) - set(by_id)
        if missing:
            raise ValueError(f"Task {task.id} references missing dependencies: {sorted(missing)}")

    completed: set[int] = set()
    remaining = set(by_id)
    layers: List[List[PlanTask]] = []
    while remaining:
        ready_ids = sorted(
            task_id for task_id in remaining
            if set(by_id[task_id].depends_on).issubset(completed)
        )
        if not ready_ids:
            raise ValueError("Execution plan contains a dependency cycle")
        layers.append([by_id[task_id] for task_id in ready_ids])
        completed.update(ready_ids)
        remaining.difference_update(ready_ids)
    return layers


async def orchestrate_dag_execution(
    client: QdrantClient,
    session: AsyncSession,
    user: User,
    query: str,
    history: Optional[List[ChatMessage]] = None,
    retrieval_top_k: int = 5,
) -> OrchestratorResponse:
    """Plan and execute a validated DAG in concurrent topological layers.

    Each task in a concurrent layer receives its own AsyncSession because
    SQLAlchemy AsyncSession instances are not safe for concurrent use.
    """
    plan = await plan_and_decompose_query(query=query, history=history, user_role=user.role.value)
    layers = _topological_layers(plan.tasks)
    task_outputs: Dict[int, Any] = {}
    all_citations: List[str] = []

    async def run_task(task: PlanTask, layer_size: int) -> tuple[int, Any, List[str]]:
        upstream = {dep_id: task_outputs[dep_id] for dep_id in task.depends_on}
        if layer_size > 1:
            async with async_session() as isolated_session:
                out, cites = await execute_task(task, client, isolated_session, user, upstream, retrieval_top_k=retrieval_top_k, history=history)
        else:
            out, cites = await execute_task(task, client, session, user, upstream, retrieval_top_k=retrieval_top_k, history=history)
        return task.id, out, cites

    for layer in layers:
        # A task is marked complete only after its output has been produced.
        results = await asyncio.gather(*(run_task(task, len(layer)) for task in layer))
        for task_id, output, citations in results:
            task_outputs[task_id] = output
            task = next(item for item in plan.tasks if item.id == task_id)
            task.status = "COMPLETED"
            task.output = output
            task.citations = citations
            all_citations.extend(citations)

    final_text, citation_map, evidence_chunks = synthesis_join_node(plan, task_outputs, all_citations)

    overall_confidence = ConfidenceLevel.HIGH
    action_draft_payload = None
    for output in task_outputs.values():
        if isinstance(output, SearchResult):
            if output.confidence == ConfidenceLevel.LOW:
                overall_confidence = ConfidenceLevel.LOW
            elif output.confidence == ConfidenceLevel.FUZZY and overall_confidence == ConfidenceLevel.HIGH:
                overall_confidence = ConfidenceLevel.FUZZY
        elif isinstance(output, dict) and output.get("status") == "ACTION_DRAFT_PENDING" and output.get("draft"):
            action_draft_payload = output["draft"]

    # Citation anchors are assigned in synthesis order; this is the canonical list sent to the UI.
    anchored_citations = list(citation_map.values())
    reranker_scores: List[float] = []
    for output in task_outputs.values():
        if isinstance(output, SearchResult):
            if output.reranker_scores:
                reranker_scores.extend(float(s) for s in output.reranker_scores)
            elif output.chunks:
                reranker_scores.extend(float(chunk.score) for chunk in output.chunks)
            elif output.top_score is not None:
                reranker_scores.append(float(output.top_score))
    return OrchestratorResponse(
        plan=plan,
        final_response=final_text,
        citations=anchored_citations,
        citation_map=citation_map,
        evidence_chunks=evidence_chunks,
        action_draft=action_draft_payload,
        confidence=overall_confidence,
        reranker_scores=reranker_scores,
        top_reranker_score=max(reranker_scores) if reranker_scores else None,
    )
