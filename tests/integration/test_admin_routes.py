"""
Integration Tests for Admin API Endpoints (Phase 7 — PRD 10.6, Decision D26, D34)

Tests:
  - Strict 401 Unauthorized for unauthenticated requests.
  - Strict 403 Forbidden for Employee and Manager roles (Decision D23, D26).
  - Admin (Role.ADMIN) endpoints:
    - GET  /api/v1/admin/health
    - GET  /api/v1/admin/audit-logs
    - GET  /api/v1/admin/analytics/queries
    - GET  /api/v1/admin/analytics/feedback
    - GET  /api/v1/admin/analytics/tokens
    - GET  /api/v1/admin/documents/{doc_id}/status
    - POST /api/v1/admin/documents/{doc_id}/reindex
"""

import io
from unittest.mock import patch
import uuid
import pytest
from httpx import ASGITransport, AsyncClient

from backend.main import app


async def get_token(client: AsyncClient, identifier: str, password: str) -> str:
    """Helper to log in and retrieve a Bearer token."""
    res = await client.post(
        "/api/v1/auth/login",
        json={"identifier": identifier, "password": password},
    )
    assert res.status_code == 200
    return res.json()["access_token"]


@pytest.mark.asyncio
async def test_admin_routes_unauthenticated_rejected():
    """Verifies that all admin routes reject requests without a token with 401."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.get("/api/v1/admin/health")
        assert res.status_code == 401

        res = await client.get("/api/v1/admin/audit-logs")
        assert res.status_code == 401

        res = await client.get("/api/v1/admin/analytics/queries")
        assert res.status_code == 401


@pytest.mark.asyncio
async def test_admin_routes_employee_forbidden():
    """Verifies that an Employee cannot access administrative endpoints (403)."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # rahul is an Employee in default seed
        token = await get_token(client, "rahul", "employee123")
        headers = {"Authorization": f"Bearer {token}"}

        res = await client.get("/api/v1/admin/health", headers=headers)
        assert res.status_code == 403

        res = await client.get("/api/v1/admin/audit-logs", headers=headers)
        assert res.status_code == 403

        res = await client.get("/api/v1/admin/analytics/tokens", headers=headers)
        assert res.status_code == 403


@pytest.mark.asyncio
async def test_admin_routes_manager_forbidden():
    """Verifies that a Manager cannot access administrative endpoints (403)."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # priya is a Manager in default seed
        token = await get_token(client, "priya", "manager123")
        headers = {"Authorization": f"Bearer {token}"}

        res = await client.get("/api/v1/admin/health", headers=headers)
        assert res.status_code == 403

        res = await client.get("/api/v1/admin/audit-logs", headers=headers)
        assert res.status_code == 403


@pytest.mark.asyncio
async def test_admin_health_endpoint():
    """Verifies that an Admin can query the system health telemetry endpoint."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        token = await get_token(client, "admin", "admin123")
        headers = {"Authorization": f"Bearer {token}"}

        res = await client.get("/api/v1/admin/health", headers=headers)
        assert res.status_code == 200
        data = res.json()
        assert "overall" in data
        assert "services" in data
        assert "postgres" in data["services"]
        assert "redis" in data["services"]
        assert "qdrant" in data["services"]


@pytest.mark.asyncio
async def test_admin_audit_logs_endpoint():
    """Verifies that an Admin can query paginated audit logs."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        token = await get_token(client, "admin", "admin123")
        headers = {"Authorization": f"Bearer {token}"}

        res = await client.get("/api/v1/admin/audit-logs?limit=10&offset=0", headers=headers)
        assert res.status_code == 200
        data = res.json()
        assert "total_returned" in data
        assert "logs" in data
        assert isinstance(data["logs"], list)


@pytest.mark.asyncio
async def test_admin_analytics_endpoints():
    """Verifies that an Admin can query queries, feedback, and token analytics."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        token = await get_token(client, "admin", "admin123")
        headers = {"Authorization": f"Bearer {token}"}

        # Queries analytics
        res_q = await client.get("/api/v1/admin/analytics/queries?days=7", headers=headers)
        assert res_q.status_code == 200
        data_q = res_q.json()
        assert "total_queries" in data_q
        assert "daily_counts" in data_q

        # Feedback analytics
        res_fb = await client.get("/api/v1/admin/analytics/feedback?days=7", headers=headers)
        assert res_fb.status_code == 200
        data_fb = res_fb.json()
        assert "thumbs_up" in data_fb
        assert "thumbs_down" in data_fb

        # Tokens analytics
        res_tk = await client.get("/api/v1/admin/analytics/tokens?days=7", headers=headers)
        assert res_tk.status_code == 200
        data_tk = res_tk.json()
        assert "grand_total_tokens" in data_tk
        assert "by_provider_model" in data_tk


@pytest.mark.asyncio
async def test_admin_document_status_and_reindex():
    """Verifies document status inspection and reindex trigger endpoints."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        token = await get_token(client, "admin", "admin123")
        headers = {"Authorization": f"Bearer {token}"}

        # 1. Non-existent doc status returns 404
        random_id = uuid.uuid4()
        res_404 = await client.get(f"/api/v1/admin/documents/{random_id}/status", headers=headers)
        assert res_404.status_code == 404

        # 2. Upload a test document first
        uid = uuid.uuid4().hex[:6]
        content = f"# Admin Integration Test Doc {uid}\nContent details here.".encode("utf-8")
        files = {
            "file": (f"Admin_Test_{uid}.md", io.BytesIO(content), "text/markdown"),
        }
        data = {
            "department": "Engineering",
            "clearance_level": "Internal",
            "title": f"Admin Test {uid}",
        }
        res_upload = await client.post("/api/v1/documents/upload", headers=headers, files=files, data=data)
        assert res_upload.status_code == 201
        doc_id = res_upload.json()["id"]

        # 3. Query document status
        res_status = await client.get(f"/api/v1/admin/documents/{doc_id}/status", headers=headers)
        assert res_status.status_code == 200
        status_data = res_status.json()
        assert status_data["id"] == doc_id
        assert status_data["filename"] == f"Admin_Test_{uid}.md"

        # 4. Trigger reindex
        with patch("backend.worker.tasks.reindex_document_task.delay"):
            res_reindex = await client.post(f"/api/v1/admin/documents/{doc_id}/reindex", headers=headers)
            assert res_reindex.status_code == 202
            reindex_data = res_reindex.json()
            assert reindex_data["doc_id"] == doc_id
            assert reindex_data["status"] in ("REINDEX_QUEUED", "REINDEX_PENDING")
