from unittest.mock import AsyncMock, patch
import httpx
import pytest

from backend.core.rate_limiter import get_degradation_message, rate_limiter
from backend.services.integrations.github_service import GITHUB_API_BASE, GitHubService
from backend.services.integrations.google_drive_service import DRIVE_API_BASE, GoogleDriveService


@pytest.mark.asyncio
async def test_github_service_sandbox_mode():
    """Verifies GitHub service returns high-fidelity sandbox data when uncredentialed."""
    gh = GitHubService(token="", default_repo="cortex-org/cortex")
    assert gh.is_configured is False

    # 1. Search code
    search_resp = await gh.search_code("rate_limiter")
    assert search_resp["status"] == "SUCCESS"
    assert search_resp["total_count"] >= 1
    assert any("rate_limiter.py" in item["name"] for item in search_resp["items"])

    # 2. Get file contents
    file_resp = await gh.get_file_contents("backend/core/rate_limiter.py")
    assert file_resp["status"] == "SUCCESS"
    assert "Mock file content" in file_resp["content"]

    # 3. List pull requests
    pr_resp = await gh.list_pull_requests()
    assert pr_resp["status"] == "SUCCESS"
    assert pr_resp["count"] >= 1
    assert pr_resp["pull_requests"][0]["author"] == "aadishpcs"

    # 4. List issues (excluding PRs)
    issue_resp = await gh.list_issues()
    assert issue_resp["status"] == "SUCCESS"
    assert issue_resp["count"] >= 1
    assert any("Redis" in issue["title"] for issue in issue_resp["issues"])


@pytest.mark.asyncio
async def test_github_service_api_call_and_rate_limit():
    """Verifies real HTTP handling, header propagation, and 403/429 rate limit fallback."""
    gh = GitHubService(token="ghp_test_token_12345", default_repo="test-org/repo")
    assert gh.is_configured is True
    assert gh._get_headers()["Authorization"] == "Bearer ghp_test_token_12345"

    # Mock successful search response
    req_success = httpx.Request("GET", f"{GITHUB_API_BASE}/search/code")
    mock_resp_success = httpx.Response(
        status_code=200,
        request=req_success,
        json={
            "total_count": 1,
            "items": [
                {
                    "name": "config.py",
                    "path": "backend/core/config.py",
                    "repository": {"full_name": "test-org/repo"},
                    "html_url": "https://github.com/test-org/repo/blob/main/backend/core/config.py",
                }
            ],
        },
    )

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_resp_success
        res = await gh.search_code("config")
        assert res["status"] == "SUCCESS"
        assert res["items"][0]["name"] == "config.py"

    # Mock 429 rate limit response with Retry-After header
    req_rate_limit = httpx.Request("GET", f"{GITHUB_API_BASE}/search/code")
    mock_resp_rate_limit = httpx.Response(
        status_code=429,
        request=req_rate_limit,
        headers={"Retry-After": "10"},
        json={"message": "API rate limit exceeded"},
    )

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_resp_rate_limit
        throttled_res = await gh.search_code("overflow")
        assert throttled_res["status"] == "RATE_LIMITED"
        assert "Live code search is temporarily at rate limit" in throttled_res["message"]


@pytest.mark.asyncio
async def test_google_drive_service_sandbox_mode():
    """Verifies Google Drive service returns structured corporate documents when uncredentialed."""
    drive = GoogleDriveService(api_key="", oauth_token="", root_folder_id="shared_root_folder_123")
    assert drive.is_configured is False

    # 1. Search files
    search_resp = await drive.search_files("Runbook")
    assert search_resp["status"] == "SUCCESS"
    assert search_resp["total_count"] >= 1
    assert any("Runbook" in f["name"] for f in search_resp["files"])

    # 2. Get file metadata
    meta_resp = await drive.get_file_metadata("file_999")
    assert meta_resp["status"] == "SUCCESS"
    assert meta_resp["id"] == "file_999"

    # 3. Read file text
    text_resp = await drive.read_file_text("file_999")
    assert text_resp["status"] == "SUCCESS"
    assert "Enterprise Shared Document" in text_resp["content"]


@pytest.mark.asyncio
async def test_google_drive_service_api_call_and_rate_limit():
    """Verifies Google Drive HTTP search, export, and rate limiting with Retry-After."""
    drive = GoogleDriveService(api_key="ai_test_key_xyz", root_folder_id="root_folder")
    assert drive.is_configured is True

    # Mock file search response
    req_drive_search = httpx.Request("GET", f"{DRIVE_API_BASE}/files")
    mock_search_resp = httpx.Response(
        status_code=200,
        request=req_drive_search,
        json={
            "files": [
                {
                    "id": "doc_abc_123",
                    "name": "Engineering Architecture Plan.gdoc",
                    "mimeType": "application/vnd.google-apps.document",
                    "size": "50000",
                    "webViewLink": "https://docs.google.com/document/d/doc_abc_123/edit",
                    "modifiedTime": "2026-11-17T10:00:00Z",
                }
            ]
        },
    )

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_search_resp
        res = await drive.search_files("Architecture")
        assert res["status"] == "SUCCESS"
        assert res["files"][0]["name"] == "Engineering Architecture Plan.gdoc"

    # Mock rate limit (403 userRateLimitExceeded)
    req_drive_limit = httpx.Request("GET", f"{DRIVE_API_BASE}/files")
    mock_rate_limit_resp = httpx.Response(
        status_code=403,
        request=req_drive_limit,
        headers={"Retry-After": "8"},
        json={"error": {"message": "Rate limit exceeded"}},
    )

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_rate_limit_resp
        throttled_res = await drive.search_files("RateLimitTest")
        assert throttled_res["status"] == "RATE_LIMITED"
        assert "Shared Drive search is temporarily at rate limit" in throttled_res["message"]
