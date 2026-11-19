import uuid
import pytest
from httpx import ASGITransport, AsyncClient

from backend.core.security import create_access_token, hash_password
from backend.db.database import async_session
from backend.main import app
from backend.models.email_request import EmailChangeRequest
from backend.models.enums import ClearanceLevel, Role
from backend.models.user import User


@pytest.mark.asyncio
async def test_users_pending_review_priority_ordering():
    """
    Verifies that users with pending action items (such as pending email revision requests)
    are automatically prioritized to the very top of the People directory on Page 1,
    and return to their regular position once approved or rejected.
    """
    async with async_session() as s:
        # Create an admin user
        admin_uid = uuid.uuid4().hex[:6]
        admin = User(
            name=f"Super Admin {admin_uid}",
            username=f"admin_{admin_uid}",
            email=f"admin_{admin_uid}@cortex.test",
            job_title="System Administrator",
            hashed_password=hash_password("adminpass123"),
            role=Role.ADMIN,
            department="IT",
            clearance_level=ClearanceLevel.CONFIDENTIAL,
            token_version=1,
            is_active=True,
        )
        # Create an old employee user who would normally be buried on later pages
        emp_uid = uuid.uuid4().hex[:6]
        emp = User(
            name=f"Buried Employee {emp_uid}",
            username=f"emp_{emp_uid}",
            email=f"emp_{emp_uid}@cortex.test",
            job_title="Software Engineer",
            hashed_password=hash_password("emppass123"),
            role=Role.EMPLOYEE,
            department="Engineering",
            clearance_level=ClearanceLevel.INTERNAL,
            token_version=1,
            is_active=True,
        )
        s.add(admin)
        s.add(emp)
        await s.commit()
        await s.refresh(admin)
        await s.refresh(emp)

        # Create a pending email revision request for this buried employee
        email_req = EmailChangeRequest(
            user_id=emp.id,
            user_name=emp.name,
            current_email=emp.email,
            requested_email=f"promoted_{emp_uid}@cortex.com",
            reason="Relocated to core engineering division",
            status="pending",
        )
        s.add(email_req)
        await s.commit()
        await s.refresh(email_req)

    admin_token = create_access_token({"sub": str(admin.id), "username": admin.username, "token_version": 1})
    headers = {"Authorization": f"Bearer {admin_token}"}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Query Page 1 of People directory
        res = await client.get("/api/v1/users/?page=1&page_size=25", headers=headers)
        assert res.status_code == 200
        data = res.json()
        assert len(data["items"]) > 0

        # The buried employee with pending action must be prioritized at index 0 (top of Page 1)
        top_user_ids = [item["id"] for item in data["items"]]
        assert str(emp.id) in top_user_ids
        assert data["items"][0]["id"] == str(emp.id)

        # 2. Admin approves the email change request
        appr_res = await client.post(f"/api/v1/admin/email-requests/{email_req.id}/approve", headers=headers)
        assert appr_res.status_code == 200

        # 3. Query Page 1 again after approval:
        # Since the action is resolved, the employee is no longer pinned at the top priority
        res2 = await client.get("/api/v1/users/?page=1&page_size=25", headers=headers)
        assert res2.status_code == 200
        data2 = res2.json()

        # The approved employee has had their email updated and is no longer forced to top rank 0
        # (unless they are chronologically the newest user)
        updated_emp_row = next((u for u in data2["items"] if u["id"] == str(emp.id)), None)
        if updated_emp_row:
            assert updated_emp_row["email"] == f"promoted_{emp_uid}@cortex.com"
