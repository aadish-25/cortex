"""
AdminAgent — Autonomous Operational Intelligence Agent (Phase 7, PRD 10.6, Decision D26)

Equips System Administrators (Role.ADMIN) with operational chat tools:
  - inspect_audit_logs(limit, user_id, category)
  - get_document_status(filename_or_id)
  - trigger_reindex(document_id)
  - inspect_system_health()
  - replace_document_version(document_id, file_bytes)

Strict RBAC Guard: Every tool execution explicitly verifies user.role == Role.ADMIN.
Non-admin requests are refused immediately with an audit log record.
"""

import logging
import re
import uuid
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from backend.core.config import settings
from backend.models.chat import ToolAction
from backend.models.document import Document
from backend.models.enums import AuditCategory, Role
from backend.models.user import User
from backend.services.admin.admin_service import (
    get_document_status,
    inspect_system_health,
    query_audit_logs,
    replace_document_version,
    trigger_document_reindex,
)
from backend.services.auth.audit_service import log_audit_event
from backend.services.orchestration.llm_service import llm_service


class AdminToolSelection(BaseModel):
    tool_name: str = Field(
        description="One of: 'inspect_system_health', 'inspect_audit_logs', 'get_document_status', 'trigger_reindex', or 'unknown'"
    )
    category: Optional[str] = Field(
        default=None,
        description="For inspect_audit_logs: optional category ('AUTH', 'DOCUMENT', 'QUERY', 'WORKFLOW', 'ADMIN')",
    )
    identifier: Optional[str] = Field(
        default=None,
        description="For get_document_status or trigger_reindex: document UUID or filename",
    )

logger = logging.getLogger(__name__)

UUID_PATTERN = re.compile(
    r"\b([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\b",
    re.IGNORECASE,
)

# Tool metadata for LLM function calling
ADMIN_TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "inspect_system_health",
            "description": "Inspect live operational health and connectivity of PostgreSQL, Redis, and Qdrant clusters.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "inspect_audit_logs",
            "description": "Fetch recent zero-PII audit trail logs, optionally filtered by category or limit.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "Number of logs to fetch (1-100)", "default": 20},
                    "category": {"type": "string", "enum": ["AUTH", "DOCUMENT", "QUERY", "WORKFLOW", "ADMIN"], "description": "Optional category filter"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_document_status",
            "description": "Check the version, is_live status, and indexing state of a document by filename or UUID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "identifier": {"type": "string", "description": "Document filename (e.g. 'leave_policy.md') or UUID string"},
                },
                "required": ["identifier"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "trigger_reindex",
            "description": "Trigger an asynchronous re-indexing task for a specified document UUID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "document_id": {"type": "string", "description": "Document UUID to reindex"},
                },
                "required": ["document_id"],
            },
        },
    },
]


class AdminAgent:
    """
    Autonomous operational agent executing administrative chat tools with strict RBAC guards.
    """

    async def execute_tool(
        self,
        session: AsyncSession,
        user: User,
        tool_name: str,
        arguments: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Executes an admin tool under strict RBAC validation.
        Refuses execution if user.role != Role.ADMIN.
        """
        arguments = arguments or {}

        # Strict RBAC Guard (PRD 10.6)
        if user.role != Role.ADMIN:
            await log_audit_event(
                session=session,
                event_tag="ADMIN_TOOL_REFUSED",
                category=AuditCategory.ADMIN,
                user_id=user.id,
                status_code=403,
                details=f"denied_tool={tool_name}",
            )
            return {
                "status": "REFUSED",
                "error": "Permission denied: Administrative clearance required.",
            }

        logger.info("Admin tool invoked: tool=%s", tool_name)

        try:
            if tool_name == "inspect_system_health":
                health_data = await inspect_system_health(session)
                await log_audit_event(
                    session=session,
                    event_tag="ADMIN_HEALTH_CHECK",
                    category=AuditCategory.ADMIN,
                    user_id=user.id,
                    status_code=200,
                    details="Admin executed system health inspection",
                )
                return {
                    "status": "SUCCESS",
                    "tool": tool_name,
                    "data": health_data,
                }

            elif tool_name == "inspect_audit_logs":
                limit = int(arguments.get("limit", 20))
                category_str = arguments.get("category")
                category_enum = None
                if category_str:
                    try:
                        category_enum = AuditCategory(category_str.upper())
                    except ValueError:
                        category_enum = None

                logs = await query_audit_logs(
                    session=session,
                    category=category_enum,
                    limit=min(limit, 100),
                )
                await log_audit_event(
                    session=session,
                    event_tag="ADMIN_AUDIT_QUERY",
                    category=AuditCategory.ADMIN,
                    user_id=user.id,
                    status_code=200,
                    details=f"Admin queried {len(logs)} audit logs (category={category_str})",
                )
                return {
                    "status": "SUCCESS",
                    "tool": tool_name,
                    "count": len(logs),
                    "logs": [
                        {
                            "id": str(l.id),
                            "event_tag": l.event_tag,
                            "category": l.category.value,
                            "status_code": l.status_code,
                            "details": l.details,
                            "created_at": l.created_at.isoformat(),
                        }
                        for l in logs
                    ],
                }

            elif tool_name == "get_document_status":
                identifier = arguments.get("identifier", "").strip()
                if not identifier:
                    return {"status": "ERROR", "error": "Document identifier required."}

                # Try parsing as UUID
                uuid_match = UUID_PATTERN.search(identifier)
                doc = None
                if uuid_match:
                    doc_uuid = uuid.UUID(uuid_match.group(1))
                    try:
                        doc = await get_document_status(session, doc_uuid)
                    except ValueError:
                        doc = None
                else:
                    # Lookup by filename (most recent version)
                    stmt = (
                        select(Document)
                        .where(Document.filename == identifier)
                        .order_by(Document.version.desc())  # type: ignore[arg-type]
                    )
                    res = await session.execute(stmt)
                    doc = res.scalars().first()

                if not doc:
                    return {
                        "status": "NOT_FOUND",
                        "error": f"Document '{identifier}' not found in registry.",
                    }

                return {
                    "status": "SUCCESS",
                    "tool": tool_name,
                    "document": {
                        "id": str(doc.id),
                        "filename": doc.filename,
                        "version": doc.version,
                        "status": doc.status.value,
                        "is_live": doc.is_live,
                        "department": doc.department,
                        "clearance_level": doc.clearance_level.value,
                        "uploaded_at": doc.uploaded_at.isoformat() if doc.uploaded_at else None,
                    },
                }

            elif tool_name == "trigger_reindex":
                raw_id = arguments.get("document_id", "")
                uuid_match = UUID_PATTERN.search(str(raw_id))
                if not uuid_match:
                    return {"status": "ERROR", "error": f"Invalid document UUID: {raw_id}"}

                doc_id = uuid.UUID(uuid_match.group(1))
                reindex_result = await trigger_document_reindex(session, doc_id)
                await log_audit_event(
                    session=session,
                    event_tag="ADMIN_REINDEX_TRIGGER",
                    category=AuditCategory.ADMIN,
                    user_id=user.id,
                    status_code=200,
                    details=f"Triggered reindex for document {doc_id}",
                )
                return {
                    "status": "SUCCESS",
                    "tool": tool_name,
                    "result": reindex_result,
                }

            elif tool_name == "replace_document_version":
                raw_id = arguments.get("document_id", "")
                uuid_match = UUID_PATTERN.search(str(raw_id))
                if not uuid_match:
                    return {"status": "ERROR", "error": f"Invalid document UUID: {raw_id}"}

                doc_id = uuid.UUID(uuid_match.group(1))
                file_bytes = arguments.get("file_bytes", b"")
                if isinstance(file_bytes, str):
                    file_bytes = file_bytes.encode("utf-8")

                replace_res = await replace_document_version(
                    session=session,
                    doc_id=doc_id,
                    file_bytes=file_bytes,
                    new_filename=arguments.get("new_filename"),
                )
                return {
                    "status": "SUCCESS",
                    "tool": tool_name,
                    "result": replace_res,
                }

            else:
                return {"status": "UNKNOWN_TOOL", "error": f"Tool '{tool_name}' is not recognized."}

        except Exception as exc:
            logger.error("Admin tool '%s' execution failed: %s", tool_name, exc, exc_info=True)
            return {"status": "EXECUTION_ERROR", "error": str(exc)}

    async def execute_from_query(
        self,
        session: AsyncSession,
        user: User,
        query: str,
        tool_action: Optional[ToolAction] = None,
    ) -> Dict[str, Any]:
        """Map admin operational requests to the narrow tool catalog.
        PRIMARY: Use LLM-emitted `tool_action` or Fast LLM `AdminToolSelection` Pydantic classifier.
        FALLBACK (only when LLM is offline/unavailable): keyword heuristics.
        """
        normalized = query.strip()
        lowered = normalized.lower()
        if user.role != Role.ADMIN:
            result = await self.execute_tool(session, user, "inspect_system_health", {})
            return {"status": "ADMIN_TOOL_RESULT", "message": self.format_response("inspect_system_health", result)}

        tool_name: Optional[str] = None
        arguments: Dict[str, Any] = {}

        # 1. Primary path A: Structured tool_action already chosen by LLM query planner
        if tool_action == ToolAction.ADMIN_HEALTH:
            tool_name, arguments = "inspect_system_health", {}
        elif tool_action == ToolAction.ADMIN_AUDIT_LOGS:
            category = next((c for c in ("AUTH", "DOCUMENT", "QUERY", "WORKFLOW", "ADMIN") if c.lower() in lowered), None)
            tool_name, arguments = "inspect_audit_logs", {"limit": 20, "category": category}
        elif tool_action == ToolAction.ADMIN_REINDEX:
            match = UUID_PATTERN.search(normalized)
            if not match:
                return {"status": "ADMIN_TOOL_RESULT", "message": "Provide the document ID to reindex it. You can copy it from Admin → Documents."}
            tool_name, arguments = "trigger_reindex", {"document_id": match.group(1)}
        elif tool_action == ToolAction.ADMIN_DOC_STATUS:
            match = UUID_PATTERN.search(normalized)
            filename_match = re.search(r"[\w .()_-]+\.(?:pdf|docx|md|txt)\b", normalized, re.IGNORECASE)
            identifier = match.group(1) if match else filename_match.group(0).strip() if filename_match else ""
            if not identifier:
                return {"status": "ADMIN_TOOL_RESULT", "message": "Specify a document ID or filename so I can inspect its status."}
            tool_name, arguments = "get_document_status", {"identifier": identifier}

        # 2. Primary path B: Fast LLM structured classification when called directly without planner tool_action
        if tool_name is None and llm_service.is_configured():
            try:
                selection = await llm_service.generate_structured(
                    prompt=f"Admin request: {normalized}",
                    response_model=AdminToolSelection,
                    system_prompt=(
                        "Select the exact admin tool for this operational request: "
                        "'inspect_system_health', 'inspect_audit_logs', 'get_document_status', 'trigger_reindex', or 'unknown'. "
                        "Extract optional audit category or document UUID/filename identifier if mentioned."
                    ),
                    model=settings.FAST_ROUTING_MODEL,
                )
                if selection and selection.tool_name in {"inspect_system_health", "inspect_audit_logs", "get_document_status", "trigger_reindex"}:
                    tool_name = selection.tool_name
                    if tool_name == "inspect_audit_logs":
                        arguments = {"limit": 20, "category": selection.category}
                    elif tool_name == "trigger_reindex":
                        uuid_match = UUID_PATTERN.search(selection.identifier or normalized)
                        if not uuid_match:
                            return {"status": "ADMIN_TOOL_RESULT", "message": "Provide the document ID to reindex it. You can copy it from Admin → Documents."}
                        arguments = {"document_id": uuid_match.group(1)}
                    elif tool_name == "get_document_status":
                        ident = (selection.identifier or "").strip()
                        if not ident:
                            return {"status": "ADMIN_TOOL_RESULT", "message": "Specify a document ID or filename so I can inspect its status."}
                        arguments = {"identifier": ident}
            except Exception as exc:
                logger.warning("LLM AdminToolSelection failed (%s); falling back to heuristic", exc)

        # 3. Offline / error fallback ONLY: keyword heuristic
        if tool_name is None:
            if any(term in lowered for term in ("system health", "service health", "infrastructure health", "health of", "redis status", "qdrant status", "postgres status", "database status")) or re.search(r"\b(healthy|health check)\b", lowered):
                tool_name, arguments = "inspect_system_health", {}
            elif "audit" in lowered:
                category = next((c for c in ("AUTH", "DOCUMENT", "QUERY", "WORKFLOW", "ADMIN") if c.lower() in lowered), None)
                tool_name, arguments = "inspect_audit_logs", {"limit": 20, "category": category}
            elif any(term in lowered for term in ("reindex", "re-index", "retry ingestion")):
                match = UUID_PATTERN.search(normalized)
                if not match:
                    return {"status": "ADMIN_TOOL_RESULT", "message": "Provide the document ID to reindex it. You can copy it from Admin → Documents."}
                tool_name, arguments = "trigger_reindex", {"document_id": match.group(1)}
            elif any(term in lowered for term in ("document status", "indexing status", "status of document", "check document")):
                match = UUID_PATTERN.search(normalized)
                filename_match = re.search(r"[\w .()_-]+\.(?:pdf|docx|md|txt)\b", normalized, re.IGNORECASE)
                identifier = match.group(1) if match else filename_match.group(0).strip() if filename_match else ""
                if not identifier:
                    return {"status": "ADMIN_TOOL_RESULT", "message": "Specify a document ID or filename so I can inspect its status."}
                tool_name, arguments = "get_document_status", {"identifier": identifier}
            else:
                return {"status": "ADMIN_TOOL_RESULT", "message": "I can inspect system health, audit logs, document status, or trigger document reindexing. Use the Admin workspace for user and document management."}

        result = await self.execute_tool(session, user, tool_name, arguments)
        return {"status": "ADMIN_TOOL_RESULT", "message": self.format_response(tool_name, result)}

    def format_response(self, tool_name: str, result: Dict[str, Any]) -> str:
        """
        Formats tool output into structured markdown for the chat interface.
        """
        if result.get("status") == "REFUSED":
            return f"**Administrative Access Refused:** {result.get('error')}"

        if result.get("status") != "SUCCESS":
            return f"**Admin Tool Error:** {result.get('error', 'Execution failed')}"

        if tool_name == "inspect_system_health":
            data = result.get("data", {})
            overall = data.get("overall", "UNKNOWN")
            services = data.get("services", {})
            lines = [f"### System Health Status: **{overall}**\n"]
            for sname, sinfo in services.items():
                lines.append(f"- **{sname.title()}**: `{sinfo.get('status')}` ({sinfo.get('detail')})")
            return "\n".join(lines)

        elif tool_name == "inspect_audit_logs":
            logs = result.get("logs", [])
            if not logs:
                return "No recent audit logs found matching criteria."
            lines = [f"### Recent Audit Trail ({len(logs)} records)\n"]
            for l in logs[:10]:
                lines.append(f"- `{l['created_at'][:19]}` | **{l['category']}** | `{l['event_tag']}` (HTTP {l['status_code']}): {l.get('details') or ''}")
            return "\n".join(lines)

        elif tool_name == "get_document_status":
            doc = result.get("document", {})
            return (
                f"### Document Status: `{doc.get('filename')}`\n"
                f"- **ID**: `{doc.get('id')}`\n"
                f"- **Version**: `v{doc.get('version')}`\n"
                f"- **Status**: `{doc.get('status')}`\n"
                f"- **Is Live**: `{'Yes' if doc.get('is_live') else 'No'}`\n"
                f"- **Department**: `{doc.get('department')}`\n"
                f"- **Clearance**: `{doc.get('clearance_level')}`"
            )

        elif tool_name == "trigger_reindex":
            r = result.get("result", {})
            return f"**Re-indexing Dispatched**: Document version `{r.get('version')}` status is now `{r.get('status')}`."

        elif tool_name == "replace_document_version":
            r = result.get("result", {})
            return f"**New Version Staged**: Document version `{r.get('version')}` staged for ingestion."

        return str(result)


admin_agent = AdminAgent()
