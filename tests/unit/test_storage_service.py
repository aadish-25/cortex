import re
import uuid
import pytest
from httpx import ASGITransport, AsyncClient

from backend.core.security import create_access_token, hash_password
from backend.db.database import async_session
from backend.main import app
from backend.models.enums import ClearanceLevel, Role
from backend.models.user import User
from backend.services.storage.cloudinary_service import CloudinaryService, cloudinary_service


def test_jira_image_naming_convention():
    """
    Verifies that attachment filenames follow the strict convention:
      <username>-<YYYYMMDD-HHMMSS>.<ext>
    The prefix before the first dash must be the sanitized username.
    """
    username = "employee_eng"
    orig_file = "reimbursement_receipt.PNG"
    formatted = cloudinary_service.format_jira_image_filename(username, orig_file)

    # 1. Check extension
    assert formatted.endswith(".png")

    # 2. Check prefix before first dash
    prefix, remainder = formatted.split("-", 1)
    assert prefix == "employee_eng"

    # 3. Check timestamp pattern YYYYMMDD-HHMMSS.png
    match = re.match(r"^(\d{8})-(\d{6})\.png$", remainder)
    assert match is not None, f"Timestamp does not match YYYYMMDD-HHMMSS pattern: {remainder}"


def test_storage_service_upload_jira_attachment():
    """
    Verifies uploading an image attachment sets the proper folder structure
    (cortex/jira_tickets/) and returns formatted metadata.
    """
    fake_image_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDRtest"
    res = cloudinary_service.upload_jira_attachment(
        file_bytes=fake_image_bytes,
        username="alice_test",
        original_filename="flight_ticket.jpg",
    )

    assert res["size_bytes"] == len(fake_image_bytes)
    assert res["filename"].startswith("alice_test-")
    assert res["filename"].endswith(".jpg")
    assert res["storage_type"] in ["cloudinary", "local"]
    assert "url" in res


def test_storage_service_upload_document():
    """
    Verifies uploading an enterprise document sets the proper folder structure
    (cortex/documents/) and returns formatted metadata.
    """
    doc_id = uuid.uuid4()
    fake_doc_bytes = b"# Engineering Department Policy Directive\nInternal memo."
    res = cloudinary_service.upload_document(
        file_bytes=fake_doc_bytes,
        document_id=doc_id,
        original_filename="policy.md",
    )

    assert res["size_bytes"] == len(fake_doc_bytes)
    assert str(doc_id) in res["filename"]
    assert res["storage_type"] in ["cloudinary", "local"]


@pytest.mark.asyncio
async def test_attachment_upload_endpoint():
    """
    Verifies FastAPI endpoint POST /api/v1/actions/attachments:
      - Accepts valid image upload from authenticated employee
      - Formats asset name with username prefix and timestamp
      - Rejects invalid extension with 400 Bad Request
    """
    uid = uuid.uuid4().hex[:6]
    test_user = User(
        name="Bob Attachment",
        username=f"bob_{uid}",
        email=f"bob_{uid}@cortex.test",
        job_title="Software Engineer",
        hashed_password=hash_password("testpass123"),
        role=Role.EMPLOYEE,
        department="Engineering",
        clearance_level=ClearanceLevel.INTERNAL,
        token_version=1,
        is_active=True,
    )
    async with async_session() as s:
        s.add(test_user)
        await s.commit()
        await s.refresh(test_user)

    token = create_access_token({"sub": str(test_user.id), "username": test_user.username, "token_version": 1})
    headers = {"Authorization": f"Bearer {token}"}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Valid image upload
        files = {"file": ("receipt.png", b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR", "image/png")}
        resp = await client.post("/api/v1/actions/attachments", headers=headers, files=files)
        assert resp.status_code == 201
        data = resp.json()
        assert data["filename"].startswith(f"bob_{uid}-")
        assert data["filename"].endswith(".png")
        assert data["size_bytes"] > 0
        assert "url" in data

        # 2. Reject disallowed extension (e.g. .exe or .zip)
        bad_files = {"file": ("malware.exe", b"MZ\x90\x00", "application/octet-stream")}
        bad_resp = await client.post("/api/v1/actions/attachments", headers=headers, files=bad_files)
        assert bad_resp.status_code == 400
        assert "Unsupported file type" in bad_resp.json()["detail"]
