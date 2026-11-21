import base64
import logging
from typing import Any, Dict, List, Optional
import httpx

from backend.core.config import settings
from backend.core.logging_config import is_test_environment
from backend.core.rate_limiter import get_degradation_message, rate_limiter

logger = logging.getLogger(__name__)

GITHUB_API_BASE = "https://api.github.com"


class GitHubService:
    """
    Production Async GitHub Client & MCP Integration Node (Phase 6.2).
    Features:
      - Read-only codebase discovery, issue triaging, and PR changelogs
      - Scoped token authorization (User OAuth or Personal Access Token)
      - Integrated token-bucket rate limiter (60 calls/min)
      - Upstream 403/429 Retry-After inspection and graceful SSE degradation
      - In-memory demonstration mock for uncredentialed development environments
    """

    def __init__(
        self,
        token: Optional[str] = None,
        default_repo: Optional[str] = None,
    ) -> None:
        self.token = (token if token is not None else settings.GITHUB_API_TOKEN).strip()
        self.default_repo = (default_repo if default_repo is not None else settings.GITHUB_DEFAULT_REPO).strip()

    @property
    def is_configured(self) -> bool:
        return bool(self.token)

    def _get_headers(self) -> Dict[str, str]:
        headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "Cortex-Enterprise-Agent/1.0",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    async def _check_rate_limit(self) -> Optional[Dict[str, Any]]:
        """Checks if GitHub API calls are currently throttled."""
        if not is_test_environment() and await rate_limiter.is_rate_limited("github"):
            logger.warning("GitHub rate limit active; bypassing external call with degradation.")
            return {
                "status": "RATE_LIMITED",
                "message": get_degradation_message("github"),
                "data": [],
            }
        return None

    async def search_code(
        self,
        query: str,
        repo: Optional[str] = None,
        per_page: int = 5,
    ) -> Dict[str, Any]:
        """
        Searches codebase files using GitHub Search API.
        Scoped to specific repository or default repo.
        """
        throttled = await self._check_rate_limit()
        if throttled:
            return throttled

        target_repo = repo or self.default_repo
        search_query = f"{query} repo:{target_repo}" if target_repo else query

        if not self.is_configured:
            # High-fidelity mock for uncredentialed dev/sandbox environments
            logger.info("GitHub API token not set; returning sandbox search results")
            return {
                "status": "SUCCESS",
                "query": search_query,
                "total_count": 2,
                "items": [
                    {
                        "name": "auth_service.py",
                        "path": "backend/services/auth_service.py",
                        "repository": target_repo or "cortex-org/cortex",
                        "html_url": f"https://github.com/{target_repo or 'cortex-org/cortex'}/blob/main/backend/services/auth_service.py",
                        "snippet": f"...matches for '{query}' in authentication token verification logic...",
                    },
                    {
                        "name": "rate_limiter.py",
                        "path": "backend/core/rate_limiter.py",
                        "repository": target_repo or "cortex-org/cortex",
                        "html_url": f"https://github.com/{target_repo or 'cortex-org/cortex'}/blob/main/backend/core/rate_limiter.py",
                        "snippet": f"...token bucket logic handling '{query}' rate limits...",
                    },
                ],
            }

        await rate_limiter.acquire("github", cost=1)

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{GITHUB_API_BASE}/search/code",
                    headers=self._get_headers(),
                    params={"q": search_query, "per_page": per_page},
                )
                if resp.status_code in (403, 429):
                    await rate_limiter.handle_upstream_rate_limit("github", headers=dict(resp.headers))
                    return {
                        "status": "RATE_LIMITED",
                        "message": get_degradation_message("github"),
                        "data": [],
                    }
                resp.raise_for_status()
                data = resp.json()
                items = [
                    {
                        "name": item.get("name"),
                        "path": item.get("path"),
                        "repository": item.get("repository", {}).get("full_name"),
                        "html_url": item.get("html_url"),
                    }
                    for item in data.get("items", [])[:per_page]
                ]
                return {
                    "status": "SUCCESS",
                    "query": search_query,
                    "total_count": data.get("total_count", len(items)),
                    "items": items,
                }
        except httpx.HTTPError as exc:
            logger.error("GitHub search_code failed: %s", exc)
            return {
                "status": "ERROR",
                "message": f"GitHub search error: {str(exc)}",
                "items": [],
            }

    async def get_file_contents(
        self,
        path: str,
        repo: Optional[str] = None,
        ref: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Fetches raw content and metadata for a repository file."""
        throttled = await self._check_rate_limit()
        if throttled:
            return throttled

        target_repo = repo or self.default_repo or "cortex-org/cortex"

        if not self.is_configured:
            return {
                "status": "SUCCESS",
                "repository": target_repo,
                "path": path,
                "ref": ref or "main",
                "size_bytes": 1024,
                "html_url": f"https://github.com/{target_repo}/blob/{ref or 'main'}/{path}",
                "content": f"# Mock file content for {path} in {target_repo}\n# Retrieved via Cortex GitHub Read-Only Node\n",
            }

        await rate_limiter.acquire("github", cost=1)

        try:
            params = {"ref": ref} if ref else {}
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{GITHUB_API_BASE}/repos/{target_repo}/contents/{path}",
                    headers=self._get_headers(),
                    params=params,
                )
                if resp.status_code in (403, 429):
                    await rate_limiter.handle_upstream_rate_limit("github", headers=dict(resp.headers))
                    return {
                        "status": "RATE_LIMITED",
                        "message": get_degradation_message("github"),
                        "content": None,
                    }
                resp.raise_for_status()
                data = resp.json()
                raw_encoded = data.get("content", "")
                decoded = ""
                if raw_encoded and data.get("encoding") == "base64":
                    decoded = base64.b64decode(raw_encoded).decode("utf-8", errors="replace")

                return {
                    "status": "SUCCESS",
                    "repository": target_repo,
                    "path": path,
                    "sha": data.get("sha"),
                    "size_bytes": data.get("size"),
                    "html_url": data.get("html_url"),
                    "content": decoded,
                }
        except httpx.HTTPError as exc:
            logger.error("GitHub get_file_contents failed: %s", exc)
            return {
                "status": "ERROR",
                "message": f"GitHub file read error: {str(exc)}",
                "content": None,
            }

    async def list_pull_requests(
        self,
        repo: Optional[str] = None,
        state: str = "open",
        per_page: int = 5,
    ) -> Dict[str, Any]:
        """Lists repository pull requests with author and merge status."""
        throttled = await self._check_rate_limit()
        if throttled:
            return throttled

        target_repo = repo or self.default_repo or "cortex-org/cortex"

        if not self.is_configured:
            return {
                "status": "SUCCESS",
                "repository": target_repo,
                "count": 2,
                "pull_requests": [
                    {
                        "number": 104,
                        "title": "feat(memory): Two-tier long-term memory architecture and rolling compaction",
                        "author": "aadishpcs",
                        "state": state,
                        "html_url": f"https://github.com/{target_repo}/pull/104",
                        "created_at": "2026-11-17T12:00:00Z",
                    },
                    {
                        "number": 103,
                        "title": "feat(jsm): Atlassian Jira Service Management universal ticketing engine",
                        "author": "aadishpcs",
                        "state": state,
                        "html_url": f"https://github.com/{target_repo}/pull/103",
                        "created_at": "2026-11-16T15:30:00Z",
                    },
                ],
            }

        await rate_limiter.acquire("github", cost=1)

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{GITHUB_API_BASE}/repos/{target_repo}/pulls",
                    headers=self._get_headers(),
                    params={"state": state, "per_page": per_page},
                )
                if resp.status_code in (403, 429):
                    await rate_limiter.handle_upstream_rate_limit("github", headers=dict(resp.headers))
                    return {
                        "status": "RATE_LIMITED",
                        "message": get_degradation_message("github"),
                        "pull_requests": [],
                    }
                resp.raise_for_status()
                prs = resp.json()
                items = [
                    {
                        "number": pr.get("number"),
                        "title": pr.get("title"),
                        "author": pr.get("user", {}).get("login"),
                        "state": pr.get("state"),
                        "html_url": pr.get("html_url"),
                        "created_at": pr.get("created_at"),
                    }
                    for pr in prs[:per_page]
                ]
                return {
                    "status": "SUCCESS",
                    "repository": target_repo,
                    "count": len(items),
                    "pull_requests": items,
                }
        except httpx.HTTPError as exc:
            logger.error("GitHub list_pull_requests failed: %s", exc)
            return {
                "status": "ERROR",
                "message": f"GitHub PR lookup error: {str(exc)}",
                "pull_requests": [],
            }

    async def list_issues(
        self,
        repo: Optional[str] = None,
        state: str = "open",
        per_page: int = 5,
    ) -> Dict[str, Any]:
        """Lists repository issues (filtering out pull requests)."""
        throttled = await self._check_rate_limit()
        if throttled:
            return throttled

        target_repo = repo or self.default_repo or "cortex-org/cortex"

        if not self.is_configured:
            return {
                "status": "SUCCESS",
                "repository": target_repo,
                "count": 2,
                "issues": [
                    {
                        "number": 88,
                        "title": "Investigate Redis stream memory growth in high-concurrency SSE streams",
                        "author": "aadishpcs",
                        "labels": ["bug", "infrastructure"],
                        "state": state,
                        "html_url": f"https://github.com/{target_repo}/issues/88",
                    },
                    {
                        "number": 92,
                        "title": "Add Google Drive OAuth refresh handler in MCP node",
                        "author": "aadishpcs",
                        "labels": ["enhancement", "mcp"],
                        "state": state,
                        "html_url": f"https://github.com/{target_repo}/issues/92",
                    },
                ],
            }

        await rate_limiter.acquire("github", cost=1)

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{GITHUB_API_BASE}/repos/{target_repo}/issues",
                    headers=self._get_headers(),
                    params={"state": state, "per_page": per_page},
                )
                if resp.status_code in (403, 429):
                    await rate_limiter.handle_upstream_rate_limit("github", headers=dict(resp.headers))
                    return {
                        "status": "RATE_LIMITED",
                        "message": get_degradation_message("github"),
                        "issues": [],
                    }
                resp.raise_for_status()
                raw_issues = resp.json()
                # GitHub issues endpoint includes PRs unless filtered
                filtered = [i for i in raw_issues if "pull_request" not in i]
                items = [
                    {
                        "number": issue.get("number"),
                        "title": issue.get("title"),
                        "body": (issue.get("body") or "")[:600],
                        "author": issue.get("user", {}).get("login"),
                        "labels": [lbl.get("name") for lbl in issue.get("labels", [])],
                        "state": issue.get("state"),
                        "html_url": issue.get("html_url"),
                    }
                    for issue in filtered[:per_page]
                ]
                return {
                    "status": "SUCCESS",
                    "repository": target_repo,
                    "count": len(items),
                    "issues": items,
                }
        except httpx.HTTPError as exc:
            logger.error("GitHub list_issues failed: %s", exc)
            return {
                "status": "ERROR",
                "message": f"GitHub issue lookup error: {str(exc)}",
                "issues": [],
            }

    async def list_repo_files(
        self,
        repo: Optional[str] = None,
        limit: int = 80,
    ) -> List[str]:
        """Lists repository file paths (via Git Trees API) to help map issues to source files."""
        target_repo = repo or self.default_repo or "cortex-org/cortex"
        if not self.is_configured:
            return ["backend/services/auth_service.py", "backend/core/rate_limiter.py"]
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{GITHUB_API_BASE}/repos/{target_repo}/git/trees/HEAD",
                    headers=self._get_headers(),
                    params={"recursive": "1"},
                )
                if resp.status_code != 200:
                    return []
                tree = resp.json().get("tree", [])
                return [
                    item["path"]
                    for item in tree
                    if item.get("type") == "blob" and item.get("path")
                ][:limit]
        except Exception as exc:
            logger.warning("GitHub list_repo_files failed: %s", exc)
            return []

    async def get_commit_history(
        self,
        repo: Optional[str] = None,
        branch: Optional[str] = None,
        limit: int = 10,
    ) -> Dict[str, Any]:
        """Fetches recent commits and author logs for the target repository."""
        target_repo = repo or self.default_repo or "cortex-org/cortex"
        if not self.is_configured:
            return {
                "status": "SUCCESS",
                "repository": target_repo,
                "commits": [
                    {
                        "sha": "4cddfac",
                        "author": "aadishpcs",
                        "message": "fix(chat): handle foreign key cascades and metric unlinking in conversation deletion",
                        "date": "2026-11-21T13:05:00Z",
                    },
                    {
                        "sha": "8cb61e7",
                        "author": "aadishpcs",
                        "message": "feat(ui): integrate custom ConfirmDialog and robust error handling for chat deletion",
                        "date": "2026-11-21T13:04:00Z",
                    },
                ],
            }

        await rate_limiter.acquire("github", cost=1)
        try:
            params: Dict[str, Any] = {"per_page": min(limit, 30)}
            if branch:
                params["sha"] = branch
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{GITHUB_API_BASE}/repos/{target_repo}/commits",
                    headers=self._get_headers(),
                    params=params,
                )
                if resp.status_code in (403, 429):
                    await rate_limiter.handle_upstream_rate_limit("github", headers=dict(resp.headers))
                    return {"status": "RATE_LIMITED", "message": get_degradation_message("github"), "commits": []}
                resp.raise_for_status()
                items = [
                    {
                        "sha": c.get("sha", "")[:7],
                        "author": c.get("commit", {}).get("author", {}).get("name") or c.get("author", {}).get("login"),
                        "message": (c.get("commit", {}).get("message") or "").split("\n")[0],
                        "date": c.get("commit", {}).get("author", {}).get("date"),
                    }
                    for c in resp.json()[:limit]
                ]
                return {"status": "SUCCESS", "repository": target_repo, "commits": items}
        except httpx.HTTPError as exc:
            logger.error("GitHub get_commit_history failed: %s", exc)
            return {"status": "ERROR", "message": str(exc), "commits": []}

    async def get_issue_comments(
        self,
        issue_number: int,
        repo: Optional[str] = None,
        limit: int = 10,
    ) -> Dict[str, Any]:
        """Fetches comment discussions on a specific issue or pull request."""
        target_repo = repo or self.default_repo or "cortex-org/cortex"
        if not self.is_configured:
            return {
                "status": "SUCCESS",
                "repository": target_repo,
                "issue_number": issue_number,
                "comments": [
                    {
                        "id": 101,
                        "author": "aadishpcs",
                        "body": "Root cause identified: Qdrant query missing explicit score cutoff threshold.",
                        "created_at": "2026-11-21T10:15:00Z",
                    }
                ],
            }

        await rate_limiter.acquire("github", cost=1)
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{GITHUB_API_BASE}/repos/{target_repo}/issues/{issue_number}/comments",
                    headers=self._get_headers(),
                    params={"per_page": min(limit, 30)},
                )
                if resp.status_code in (403, 429):
                    await rate_limiter.handle_upstream_rate_limit("github", headers=dict(resp.headers))
                    return {"status": "RATE_LIMITED", "message": get_degradation_message("github"), "comments": []}
                resp.raise_for_status()
                items = [
                    {
                        "id": comment.get("id"),
                        "author": comment.get("user", {}).get("login"),
                        "body": (comment.get("body") or "")[:500],
                        "created_at": comment.get("created_at"),
                    }
                    for comment in resp.json()[:limit]
                ]
                return {"status": "SUCCESS", "repository": target_repo, "issue_number": issue_number, "comments": items}
        except httpx.HTTPError as exc:
            logger.error("GitHub get_issue_comments failed: %s", exc)
            return {"status": "ERROR", "message": str(exc), "comments": []}

    async def get_pr_diff(
        self,
        pr_number: int,
        repo: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Fetches the unified git diff or file changes for a pull request."""
        target_repo = repo or self.default_repo or "cortex-org/cortex"
        if not self.is_configured:
            return {
                "status": "SUCCESS",
                "repository": target_repo,
                "pr_number": pr_number,
                "diff": "--- a/backend/routers/chat.py\n+++ b/backend/routers/chat.py\n@@ -204,6 +204,18 @@\n+    # Foreign key cascade cleanup\n",
            }

        await rate_limiter.acquire("github", cost=1)
        try:
            headers = self._get_headers()
            headers["Accept"] = "application/vnd.github.v3.diff"
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{GITHUB_API_BASE}/repos/{target_repo}/pulls/{pr_number}",
                    headers=headers,
                )
                if resp.status_code in (403, 429):
                    await rate_limiter.handle_upstream_rate_limit("github", headers=dict(resp.headers))
                    return {"status": "RATE_LIMITED", "message": get_degradation_message("github"), "diff": ""}
                resp.raise_for_status()
                return {"status": "SUCCESS", "repository": target_repo, "pr_number": pr_number, "diff": resp.text[:3000]}
        except httpx.HTTPError as exc:
            logger.error("GitHub get_pr_diff failed: %s", exc)
            return {"status": "ERROR", "message": str(exc), "diff": ""}

    async def get_workflow_status(
        self,
        repo: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Fetches recent CI/CD GitHub Actions workflow run statuses."""
        target_repo = repo or self.default_repo or "cortex-org/cortex"
        if not self.is_configured:
            return {
                "status": "SUCCESS",
                "repository": target_repo,
                "workflows": [
                    {
                        "id": 1001,
                        "name": "CI / Unit Tests",
                        "status": "completed",
                        "conclusion": "success",
                        "updated_at": "2026-11-21T12:00:00Z",
                    }
                ],
            }

        await rate_limiter.acquire("github", cost=1)
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{GITHUB_API_BASE}/repos/{target_repo}/actions/runs",
                    headers=self._get_headers(),
                    params={"per_page": 5},
                )
                if resp.status_code in (403, 429):
                    await rate_limiter.handle_upstream_rate_limit("github", headers=dict(resp.headers))
                    return {"status": "RATE_LIMITED", "message": get_degradation_message("github"), "workflows": []}
                resp.raise_for_status()
                runs = resp.json().get("workflow_runs", [])
                items = [
                    {
                        "id": r.get("id"),
                        "name": r.get("name"),
                        "status": r.get("status"),
                        "conclusion": r.get("conclusion"),
                        "updated_at": r.get("updated_at"),
                    }
                    for r in runs[:5]
                ]
                return {"status": "SUCCESS", "repository": target_repo, "workflows": items}
        except httpx.HTTPError as exc:
            logger.error("GitHub get_workflow_status failed: %s", exc)
            return {"status": "ERROR", "message": str(exc), "workflows": []}


github_service = GitHubService()
