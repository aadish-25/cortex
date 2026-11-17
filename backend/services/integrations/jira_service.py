import asyncio
import base64
import logging
from typing import Any, Dict, List, Optional
import httpx

from backend.core.config import settings
from backend.core.logging_config import is_test_environment
from backend.core.rate_limiter import rate_limiter
from backend.models.enums import TicketPriority, TicketTaxonomy

logger = logging.getLogger(__name__)

# Default department to Jira Project Key routing
DEPARTMENT_PROJECT_MAP: Dict[str, str] = {
    "engineering": "KAN",
    "eng": "KAN",
    "it": "KAN",
    "information technology": "KAN",
    "hr": "HRSD",
    "human resources": "HRSD",
    "people": "HRSD",
    "people ops": "HRSD",
    "finance": "FNA",
    "accounts": "FNA",
    "billing": "FNA",
    "finops": "FNA",
}

# Jira Cloud Standard Priority Names
JIRA_PRIORITY_MAP: Dict[TicketPriority, str] = {
    TicketPriority.CRITICAL: "Highest",
    TicketPriority.HIGH: "High",
    TicketPriority.MEDIUM: "Medium",
    TicketPriority.LOW: "Low",
}


def build_adf_description(
    title: str,
    description: str,
    requester_name: str,
    requester_email: str,
    department: str,
    taxonomy_tier: TicketTaxonomy,
    priority: TicketPriority,
    domain_fields: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Constructs a compliant Atlassian Document Format (ADF) payload
    for Jira Cloud REST API v3.
    """
    content: List[Dict[str, Any]] = []

    # Header / Notice
    content.append({
        "type": "heading",
        "attrs": {"level": 3},
        "content": [{"type": "text", "text": "🤖 Cortex Operational Action Draft"}],
    })

    # Metadata Paragraph
    meta_elements = [
        {"type": "text", "text": "Requester: ", "marks": [{"type": "strong"}]},
        {"type": "text", "text": f"{requester_name} ({requester_email})\n"},
        {"type": "text", "text": "Department: ", "marks": [{"type": "strong"}]},
        {"type": "text", "text": f"{department}\n"},
        {"type": "text", "text": "Taxonomy Tier: ", "marks": [{"type": "strong"}]},
        {"type": "text", "text": f"{taxonomy_tier.value}\n"},
        {"type": "text", "text": "Priority: ", "marks": [{"type": "strong"}]},
        {"type": "text", "text": f"{priority.value}"},
    ]
    content.append({"type": "paragraph", "content": meta_elements})

    # Description Section
    content.append({
        "type": "heading",
        "attrs": {"level": 4},
        "content": [{"type": "text", "text": "Operational Details & Context"}],
    })
    content.append({
        "type": "paragraph",
        "content": [{"type": "text", "text": description or title}],
    })

    # Domain Fields Section (if any)
    if domain_fields:
        content.append({
            "type": "heading",
            "attrs": {"level": 4},
            "content": [{"type": "text", "text": "Domain Parameters"}],
        })
        list_items = []
        for key, val in domain_fields.items():
            clean_key = key.replace("_", " ").title()
            list_items.append({
                "type": "listItem",
                "content": [
                    {
                        "type": "paragraph",
                        "content": [
                            {"type": "text", "text": f"{clean_key}: ", "marks": [{"type": "strong"}]},
                            {"type": "text", "text": str(val)},
                        ],
                    }
                ],
            })
        content.append({"type": "bulletList", "content": list_items})

    # Footer
    content.append({
        "type": "paragraph",
        "content": [
            {
                "type": "text",
                "text": "— Auto-generated & confirmed via Cortex HITL ActionDraft Engine",
                "marks": [{"type": "em"}],
            }
        ],
    })

    return {
        "type": "doc",
        "version": 1,
        "content": content,
    }


class JiraService:
    """
    Production Async Jira Service Management (JSM) & Atlassian Cloud REST API v3 Client.
    Features:
      - Token-bucket rate limiting with 429 Retry-After inspection
      - Automatic department project key routing (KAN, HRSD, FNA)
      - Dynamic issue type discovery & fallback
      - Atlassian Document Format (ADF) description serialization
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        email: Optional[str] = None,
        token: Optional[str] = None,
    ) -> None:
        self.base_url = (base_url or settings.JIRA_BASE_URL).rstrip("/")
        self.email = (email or settings.JIRA_API_EMAIL).strip()
        self.token = (token or settings.JIRA_API_TOKEN).strip()
        self._auth_header: Optional[str] = None

        if self.email and self.token:
            auth_bytes = f"{self.email}:{self.token}".encode("utf-8")
            self._auth_header = f"Basic {base64.b64encode(auth_bytes).decode('utf-8')}"

    @property
    def is_configured(self) -> bool:
        return bool(self.base_url and self.email and self.token)

    def get_headers(self) -> Dict[str, str]:
        if not self._auth_header:
            raise ValueError("Jira credentials not configured. Please set JIRA_BASE_URL, JIRA_API_EMAIL, and JIRA_API_TOKEN.")
        return {
            "Authorization": self._auth_header,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def resolve_project_key(self, department: Optional[str]) -> str:
        """Resolves target Jira project key from department name."""
        if not department:
            return settings.JIRA_DEFAULT_PROJECT_KEY or "KAN"
        dept_norm = department.strip().lower()
        return DEPARTMENT_PROJECT_MAP.get(dept_norm, settings.JIRA_DEFAULT_PROJECT_KEY or "KAN")

    def resolve_issue_type(self, taxonomy: TicketTaxonomy, available_types: Optional[List[str]] = None) -> str:
        """
        Maps universal taxonomy to the closest available Jira issue type in project.
        In Jira team-managed projects, standard types are 'Task' and 'Bug'.
        """
        types = [t.lower() for t in available_types] if available_types else ["task", "bug"]

        if taxonomy == TicketTaxonomy.INCIDENT:
            if "bug" in types:
                return "Bug"
            if "incident" in types:
                return "Incident"

        # Standard fallback is 'Task'
        for candidate in ["task", "service request", "it help", "support"]:
            if candidate in types:
                return candidate.title()

        return available_types[0] if available_types else "Task"

    async def _make_request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        json_data: Optional[Dict[str, Any]] = None,
        max_retries: int = 3,
    ) -> Dict[str, Any]:
        """
        Executes rate-limited HTTP request to Atlassian REST API v3 with 429 retry backoff.
        """
        if not self.is_configured:
            raise RuntimeError("Jira client is not configured with valid credentials.")

        url = f"{self.base_url}{path}"
        headers = self.get_headers()

        for attempt in range(max_retries):
            # Token bucket rate limiting check
            acquired = await rate_limiter.acquire("jira", timeout=15.0)
            if not acquired:
                logger.warning("Jira rate limit acquisition timed out; retrying attempt %d", attempt + 1)
                await asyncio.sleep(1.0)

            try:
                async with httpx.AsyncClient(timeout=20.0) as client:
                    resp = await client.request(
                        method=method,
                        url=url,
                        headers=headers,
                        params=params,
                        json=json_data,
                    )

                    if resp.status_code == 429:
                        penalty = await rate_limiter.handle_upstream_rate_limit(
                            "jira",
                            headers=dict(resp.headers),
                        )
                        logger.warning("Jira returned 429 Too Many Requests. Backing off for %.2fs", penalty)
                        await asyncio.sleep(penalty)
                        continue

                    if resp.status_code in (200, 201):
                        return resp.json()

                    # Handle 400 or other errors
                    error_text = resp.text
                    logger.error("Jira API error (HTTP %d): %s", resp.status_code, error_text)
                    raise RuntimeError(f"Jira API call to {path} failed (HTTP {resp.status_code}): {error_text}")

            except httpx.RequestError as exc:
                logger.warning("HTTP connection error communicating with Jira: %s. Attempt %d/%d", exc, attempt + 1, max_retries)
                if attempt == max_retries - 1:
                    raise RuntimeError(f"Failed to connect to Jira at {url}: {exc}") from exc
                await asyncio.sleep(1.0 * (2 ** attempt))

        raise RuntimeError(f"Exceeded max retries calling Jira endpoint: {path}")

    async def get_myself(self) -> Dict[str, Any]:
        """Validates credentials and retrieves current authenticated Jira user."""
        return await self._make_request("GET", "/rest/api/3/myself")

    async def list_projects(self) -> List[Dict[str, Any]]:
        """Lists accessible projects in Jira instance."""
        res = await self._make_request("GET", "/rest/api/3/project")
        return res if isinstance(res, list) else []

    async def get_project_issue_types(self, project_key: str) -> List[str]:
        """Introspects available issue types for a target project."""
        try:
            meta = await self._make_request(
                "GET",
                "/rest/api/3/issue/createmeta",
                params={"projectKeys": project_key, "expand": "projects.issuetypes"},
            )
            projects = meta.get("projects", [])
            if projects:
                return [it.get("name") for it in projects[0].get("issuetypes", []) if it.get("name")]
        except Exception as e:
            logger.warning("Error fetching issue types for project %s: %s", project_key, e)
        return ["Task", "Bug"]

    async def create_issue(
        self,
        department: str,
        title: str,
        description: str,
        requester_name: str,
        requester_email: str,
        taxonomy_tier: TicketTaxonomy = TicketTaxonomy.SERVICE_REQUEST,
        priority: TicketPriority = TicketPriority.MEDIUM,
        domain_fields: Optional[Dict[str, Any]] = None,
        project_key_override: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Creates an issue in the appropriate Jira department queue using ADF formatting.
        Returns dict containing: 'key', 'id', 'url', 'project_key', 'issue_type'.
        """
        # In mock / test environment when credentials are dummy, return simulated response
        if is_test_environment() and (not self.is_configured or self.token == "mock_token"):
            mock_key = f"{project_key_override or 'MOCK'}-999"
            return {
                "key": mock_key,
                "id": "99999",
                "url": f"https://mock-jira.atlassian.net/browse/{mock_key}",
                "project_key": project_key_override or "MOCK",
                "issue_type": "Task",
            }

        project_key = project_key_override or self.resolve_project_key(department)
        available_types = await self.get_project_issue_types(project_key)
        issue_type_name = self.resolve_issue_type(taxonomy_tier, available_types)

        adf_description = build_adf_description(
            title=title,
            description=description,
            requester_name=requester_name,
            requester_email=requester_email,
            department=department,
            taxonomy_tier=taxonomy_tier,
            priority=priority,
            domain_fields=domain_fields,
        )

        jira_priority_name = JIRA_PRIORITY_MAP.get(priority, "Medium")

        payload: Dict[str, Any] = {
            "fields": {
                "project": {"key": project_key},
                "summary": title,
                "description": adf_description,
                "issuetype": {"name": issue_type_name},
                "priority": {"name": jira_priority_name},
            }
        }

        try:
            resp = await self._make_request("POST", "/rest/api/3/issue", json_data=payload)
        except RuntimeError as e:
            # Some team-managed projects do not support custom priority field in creation payload
            if "priority" in str(e).lower():
                logger.info("Retrying issue creation without priority field for project %s", project_key)
                del payload["fields"]["priority"]
                resp = await self._make_request("POST", "/rest/api/3/issue", json_data=payload)
            else:
                raise

        issue_key = resp.get("key")
        issue_id = resp.get("id")
        issue_url = f"{self.base_url}/browse/{issue_key}"

        logger.info(
            "Successfully created Jira ticket %s in project %s (%s)",
            issue_key,
            project_key,
            issue_url,
        )

        return {
            "key": issue_key,
            "id": issue_id,
            "url": issue_url,
            "project_key": project_key,
            "issue_type": issue_type_name,
        }

    async def get_issue(self, issue_key_or_id: str) -> Dict[str, Any]:
        """Retrieves ticket details and status from Jira."""
        if is_test_environment() and (not self.is_configured or self.token == "mock_token"):
            return {
                "key": issue_key_or_id,
                "fields": {
                    "summary": "Mock Issue Summary",
                    "status": {"name": "In Progress"},
                },
            }
        return await self._make_request("GET", f"/rest/api/3/issue/{issue_key_or_id}")


# Global singleton instance
jira_service = JiraService()
