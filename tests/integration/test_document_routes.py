import io
import uuid
import pytest
from httpx import ASGITransport, AsyncClient

from backend.main import app


async def get_token(client: AsyncClient, identifier: str, password: str) -> str:
    res = await client.post(
        "/api/v1/auth/login",
        json={"identifier": identifier, "password": password},
    )
    assert res.status_code == 200
    return res.json()["access_token"]


@pytest.mark.asyncio
async def test_admin_upload_markdown_document_success():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        admin_token = await get_token(client, "admin", "admin123")
        uid = uuid.uuid4().hex[:8]

        sample_content = (
            f"# Engineering Server Deployment Guidelines {uid}\n\n"
            f"## Section 1: Bastion Host Access\n"
            f"All engineers must connect through the bastion host with dynamic id {uid}.\n\n"
            f"## Section 2: Production Deployment Windows\n"
            f"Deployments to production occur on Tuesdays and Thursdays between 10:00 AM and 12:00 PM IST."
        ).encode("utf-8")

        files = {
            "file": (f"ENG_Server_Policy_{uid}.md", io.BytesIO(sample_content), "text/markdown"),
        }
        data = {
            "department": "Engineering",
            "clearance_level": "Internal",
            "title": f"Engineering Server Policy {uid}",
        }

        res = await client.post(
            "/api/v1/documents/upload",
            headers={"Authorization": f"Bearer {admin_token}"},
            files=files,
            data=data,
        )

        assert res.status_code == 201
        body = res.json()
        assert body["chunks_indexed"] >= 2
        assert body["is_new_version"] is False
        doc = body["document"]
        assert doc["department"] == "Engineering"
        assert doc["clearance_level"] == "Internal"
        assert doc["version"] == 1
        assert doc["status"] == "ACTIVE"
        assert doc["is_live"] is True


@pytest.mark.asyncio
async def test_upload_duplicate_hash_rejected_409():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        admin_token = await get_token(client, "admin", "admin123")
        uid = uuid.uuid4().hex[:8]

        content = f"Static test content for duplicate SHA-256 test {uid}".encode("utf-8")
        files = {
            "file": (f"duplicate_test_{uid}.txt", io.BytesIO(content), "text/plain"),
        }
        data = {
            "department": "Engineering",
            "clearance_level": "Public",
        }

        # First upload succeeds
        res1 = await client.post(
            "/api/v1/documents/upload",
            headers={"Authorization": f"Bearer {admin_token}"},
            files=files,
            data=data,
        )
        assert res1.status_code == 201

        # Second upload with identical bytes must be rejected with 409
        files2 = {
            "file": (f"duplicate_test_{uid}.txt", io.BytesIO(content), "text/plain"),
        }
        res2 = await client.post(
            "/api/v1/documents/upload",
            headers={"Authorization": f"Bearer {admin_token}"},
            files=files2,
            data=data,
        )
        assert res2.status_code == 409
        assert "identical content already exists" in res2.json()["detail"]


@pytest.mark.asyncio
async def test_upload_new_version_success():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        admin_token = await get_token(client, "admin", "admin123")
        uid = uuid.uuid4().hex[:8]
        filename = f"versioned_policy_{uid}.md"

        v1_bytes = f"# Policy {uid}\nVersion 1 content.".encode("utf-8")
        v2_bytes = f"# Policy {uid}\nVersion 2 updated content with new rules.".encode("utf-8")

        # Upload v1
        res1 = await client.post(
            "/api/v1/documents/upload",
            headers={"Authorization": f"Bearer {admin_token}"},
            files={"file": (filename, io.BytesIO(v1_bytes), "text/markdown")},
            data={"department": "Engineering", "clearance_level": "Internal"},
        )
        assert res1.status_code == 201
        assert res1.json()["document"]["version"] == 1

        # Upload v2 with same filename but new content
        res2 = await client.post(
            "/api/v1/documents/upload",
            headers={"Authorization": f"Bearer {admin_token}"},
            files={"file": (filename, io.BytesIO(v2_bytes), "text/markdown")},
            data={"department": "Engineering", "clearance_level": "Internal"},
        )
        assert res2.status_code == 201
        body2 = res2.json()
        assert body2["document"]["version"] == 2
        assert body2["is_new_version"] is True
        assert body2["document"]["status"] == "ACTIVE"
        assert body2["document"]["is_live"] is True


@pytest.mark.asyncio
async def test_employee_cannot_upload_document():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        employee_token = await get_token(client, "employee_hr", "employee123")

        res = await client.post(
            "/api/v1/documents/upload",
            headers={"Authorization": f"Bearer {employee_token}"},
            files={"file": ("hack.txt", io.BytesIO(b"hack"), "text/plain")},
            data={"department": "HR", "clearance_level": "Public"},
        )
        assert res.status_code == 403


@pytest.mark.asyncio
async def test_manager_upload_department_scoping():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        manager_token = await get_token(client, "manager.eng@cortex.local", "manager123")
        uid = uuid.uuid4().hex[:8]

        # 1. Manager uploading to own department (Engineering) succeeds
        res_ok = await client.post(
            "/api/v1/documents/upload",
            headers={"Authorization": f"Bearer {manager_token}"},
            files={"file": (f"eng_memo_{uid}.md", io.BytesIO(f"# Memo {uid}".encode()), "text/markdown")},
            data={"department": "Engineering", "clearance_level": "Internal"},
        )
        assert res_ok.status_code == 201

        # 2. Manager attempting to upload to HR is rejected with 403
        res_forbidden = await client.post(
            "/api/v1/documents/upload",
            headers={"Authorization": f"Bearer {manager_token}"},
            files={"file": (f"hr_memo_{uid}.md", io.BytesIO(b"unauthorized"), "text/markdown")},
            data={"department": "HR", "clearance_level": "Internal"},
        )
        assert res_forbidden.status_code == 403
        assert "Managers can only upload documents for their own department" in res_forbidden.json()["detail"]


@pytest.mark.asyncio
async def test_list_and_get_documents_with_3d_rbac():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        admin_token = await get_token(client, "admin", "admin123")
        uid = uuid.uuid4().hex[:8]

        # Upload an HR Confidential document
        conf_bytes = f"# Confidential {uid}\nSecret HR plans {uid}".encode("utf-8")
        hr_conf_res = await client.post(
            "/api/v1/documents/upload",
            headers={"Authorization": f"Bearer {admin_token}"},
            files={"file": (f"HR_Confidential_{uid}.md", io.BytesIO(conf_bytes), "text/markdown")},
            data={"department": "HR", "clearance_level": "Confidential"},
        )
        assert hr_conf_res.status_code == 201
        conf_doc_id = hr_conf_res.json()["document"]["id"]

        # Admin can list and see the confidential document
        admin_list = await client.get(
            "/api/v1/documents/",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert admin_list.status_code == 200
        admin_doc_ids = [d["id"] for d in admin_list.json()]
        assert conf_doc_id in admin_doc_ids

        # Engineering Manager (Clearance: Internal) cannot see HR Confidential in list
        eng_mgr_token = await get_token(client, "manager.eng@cortex.local", "manager123")
        eng_list = await client.get(
            "/api/v1/documents/",
            headers={"Authorization": f"Bearer {eng_mgr_token}"},
        )
        assert eng_list.status_code == 200
        eng_doc_ids = [d["id"] for d in eng_list.json()]
        assert conf_doc_id not in eng_doc_ids

        # Engineering Manager attempting to GET the document directly gets 403 Forbidden
        direct_get = await client.get(
            f"/api/v1/documents/{conf_doc_id}",
            headers={"Authorization": f"Bearer {eng_mgr_token}"},
        )
        assert direct_get.status_code == 403


@pytest.mark.asyncio
async def test_delete_document_success():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        admin_token = await get_token(client, "admin", "admin123")
        uid = uuid.uuid4().hex[:8]

        upload_res = await client.post(
            "/api/v1/documents/upload",
            headers={"Authorization": f"Bearer {admin_token}"},
            files={"file": (f"delete_me_{uid}.txt", io.BytesIO(b"Temporary file content"), "text/plain")},
            data={"department": "Engineering", "clearance_level": "Public"},
        )
        assert upload_res.status_code == 201
        doc_id = upload_res.json()["document"]["id"]

        # Delete document
        del_res = await client.delete(
            f"/api/v1/documents/{doc_id}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert del_res.status_code == 200
        assert del_res.json()["status"] == "deleted"

        # Subsequent GET should return 404
        get_res = await client.get(
            f"/api/v1/documents/{doc_id}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert get_res.status_code == 404
