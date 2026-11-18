import logging
from typing import Any, Dict, List, Optional
import httpx

from backend.core.config import settings
from backend.core.logging_config import is_test_environment
from backend.core.rate_limiter import get_degradation_message, rate_limiter

logger = logging.getLogger(__name__)

DRIVE_API_BASE = "https://www.googleapis.com/drive/v3"


class GoogleDriveService:
    """
    Production Async Google Drive & Workspace MCP Integration Node (Phase 6.2).
    Features:
      - Read-only search across company Shared Drives and scoped folder hierarchies
      - Document metadata retrieval and Google Docs plain-text export
      - Integrated token-bucket rate limiter (100 calls/min)
      - Upstream 403/429 Retry-After inspection and graceful degradation
      - In-memory demonstration mock for uncredentialed development environments
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        oauth_token: Optional[str] = None,
        root_folder_id: Optional[str] = None,
    ) -> None:
        self.api_key = (api_key if api_key is not None else settings.GOOGLE_DRIVE_API_KEY).strip()
        self.oauth_token = (oauth_token if oauth_token is not None else "").strip()
        self.root_folder_id = (root_folder_id if root_folder_id is not None else settings.GOOGLE_DRIVE_ROOT_FOLDER_ID).strip()

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key or self.oauth_token)

    def _get_headers(self) -> Dict[str, str]:
        headers = {
            "Accept": "application/json",
            "User-Agent": "Cortex-Enterprise-Agent/1.0",
        }
        if self.oauth_token:
            headers["Authorization"] = f"Bearer {self.oauth_token}"
        return headers

    async def _check_rate_limit(self) -> Optional[Dict[str, Any]]:
        """Checks if Google Drive API calls are currently throttled."""
        if not is_test_environment() and await rate_limiter.is_rate_limited("google_drive"):
            logger.warning("Google Drive rate limit active; bypassing external call with degradation.")
            return {
                "status": "RATE_LIMITED",
                "message": get_degradation_message("google_drive"),
                "files": [],
            }
        return None

    async def search_files(
        self,
        query: str,
        folder_id: Optional[str] = None,
        mime_type: Optional[str] = None,
        page_size: int = 5,
    ) -> Dict[str, Any]:
        """
        Searches Google Drive files within an optional folder and mimeType filter.
        """
        throttled = await self._check_rate_limit()
        if throttled:
            return throttled

        target_folder = folder_id or self.root_folder_id

        if not self.is_configured:
            logger.info("Google Drive credentials not set; returning sandbox search results")
            return {
                "status": "SUCCESS",
                "query": query,
                "folder_id": target_folder or "root",
                "total_count": 2,
                "files": [
                    {
                        "id": "1A2B3C_cortex_engineering_runbook",
                        "name": "Q4 Engineering Cloud Infrastructure Runbook.gdoc",
                        "mimeType": "application/vnd.google-apps.document",
                        "size_bytes": 45000,
                        "webViewLink": "https://docs.google.com/document/d/1A2B3C_cortex_engineering_runbook/edit",
                        "modifiedTime": "2026-11-15T10:00:00Z",
                    },
                    {
                        "id": "4D5E6F_cortex_hr_benefits_sheet",
                        "name": "2026 Indian Corporate Medical & Leave Policy.gdoc",
                        "mimeType": "application/vnd.google-apps.document",
                        "size_bytes": 32000,
                        "webViewLink": "https://docs.google.com/document/d/4D5E6F_cortex_hr_benefits_sheet/edit",
                        "modifiedTime": "2026-11-14T14:30:00Z",
                    },
                ],
            }

        await rate_limiter.acquire("google_drive", cost=1)

        # Build Google Drive search query syntax
        query_clauses = [f"name contains '{query}'", "trashed = false"]
        if target_folder:
            query_clauses.append(f"'{target_folder}' in parents")
        if mime_type:
            query_clauses.append(f"mimeType = '{mime_type}'")

        q_param = " and ".join(query_clauses)
        params: Dict[str, Any] = {
            "q": q_param,
            "pageSize": page_size,
            "fields": "files(id, name, mimeType, size, webViewLink, modifiedTime)",
        }
        if self.api_key and not self.oauth_token:
            params["key"] = self.api_key

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{DRIVE_API_BASE}/files",
                    headers=self._get_headers(),
                    params=params,
                )
                if resp.status_code in (403, 429):
                    await rate_limiter.handle_upstream_rate_limit("google_drive", headers=dict(resp.headers))
                    return {
                        "status": "RATE_LIMITED",
                        "message": get_degradation_message("google_drive"),
                        "files": [],
                    }
                resp.raise_for_status()
                data = resp.json()
                files = data.get("files", [])[:page_size]
                return {
                    "status": "SUCCESS",
                    "query": query,
                    "folder_id": target_folder,
                    "total_count": len(files),
                    "files": files,
                }
        except httpx.HTTPError as exc:
            logger.error("Google Drive search_files failed: %s", exc)
            return {
                "status": "ERROR",
                "message": f"Google Drive search error: {str(exc)}",
                "files": [],
            }

    async def get_file_metadata(self, file_id: str) -> Dict[str, Any]:
        """Retrieves metadata attributes for a specific Google Drive file."""
        throttled = await self._check_rate_limit()
        if throttled:
            return throttled

        if not self.is_configured:
            return {
                "status": "SUCCESS",
                "id": file_id,
                "name": f"File_{file_id}.gdoc",
                "mimeType": "application/vnd.google-apps.document",
                "size": "20480",
                "webViewLink": f"https://drive.google.com/file/d/{file_id}/view",
                "modifiedTime": "2026-11-17T09:00:00Z",
            }

        await rate_limiter.acquire("google_drive", cost=1)

        params: Dict[str, Any] = {"fields": "id, name, mimeType, size, webViewLink, modifiedTime, parents"}
        if self.api_key and not self.oauth_token:
            params["key"] = self.api_key

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{DRIVE_API_BASE}/files/{file_id}",
                    headers=self._get_headers(),
                    params=params,
                )
                if resp.status_code in (403, 429):
                    await rate_limiter.handle_upstream_rate_limit("google_drive", headers=dict(resp.headers))
                    return {
                        "status": "RATE_LIMITED",
                        "message": get_degradation_message("google_drive"),
                    }
                resp.raise_for_status()
                return {
                    "status": "SUCCESS",
                    **resp.json(),
                }
        except httpx.HTTPError as exc:
            logger.error("Google Drive get_file_metadata failed: %s", exc)
            return {
                "status": "ERROR",
                "message": f"Google Drive metadata error: {str(exc)}",
            }

    async def read_file_text(self, file_id: str) -> Dict[str, Any]:
        """
        Extracts plain-text representation from a Google Doc or text file.
        For Google Docs, calls Drive export endpoint with text/plain.
        """
        throttled = await self._check_rate_limit()
        if throttled:
            return throttled

        if not self.is_configured:
            return {
                "status": "SUCCESS",
                "id": file_id,
                "mimeType": "text/plain",
                "content": f"Enterprise Shared Document [{file_id}]:\n- Standard Operating Procedure for AWS and Docker deployments.\n- Confidentiality notice: Internal corporate use only.\n",
            }

        await rate_limiter.acquire("google_drive", cost=1)

        try:
            # Check file metadata to determine if export or download is required
            meta = await self.get_file_metadata(file_id)
            mime_type = meta.get("mimeType", "")

            async with httpx.AsyncClient(timeout=15.0) as client:
                if "application/vnd.google-apps" in mime_type:
                    # Google Docs/Sheets export endpoint
                    export_params: Dict[str, Any] = {"mimeType": "text/plain"}
                    if self.api_key and not self.oauth_token:
                        export_params["key"] = self.api_key
                    resp = await client.get(
                        f"{DRIVE_API_BASE}/files/{file_id}/export",
                        headers=self._get_headers(),
                        params=export_params,
                    )
                else:
                    # Binary or standard text download
                    dl_params: Dict[str, Any] = {"alt": "media"}
                    if self.api_key and not self.oauth_token:
                        dl_params["key"] = self.api_key
                    resp = await client.get(
                        f"{DRIVE_API_BASE}/files/{file_id}",
                        headers=self._get_headers(),
                        params=dl_params,
                    )

                if resp.status_code in (403, 429):
                    await rate_limiter.handle_upstream_rate_limit("google_drive", headers=dict(resp.headers))
                    return {
                        "status": "RATE_LIMITED",
                        "message": get_degradation_message("google_drive"),
                        "content": None,
                    }
                resp.raise_for_status()
                return {
                    "status": "SUCCESS",
                    "id": file_id,
                    "name": meta.get("name"),
                    "content": resp.text,
                }
        except httpx.HTTPError as exc:
            logger.error("Google Drive read_file_text failed: %s", exc)
            return {
                "status": "ERROR",
                "message": f"Google Drive export error: {str(exc)}",
                "content": None,
            }


google_drive_service = GoogleDriveService()
