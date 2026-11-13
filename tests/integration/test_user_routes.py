import uuid
import pytest
from httpx import ASGITransport, AsyncClient

from backend.main import app


async def get_auth_token(client: AsyncClient, identifier: str, password: str) -> str:
    """Helper to log in and retrieve a Bearer token."""
    res = await client.post(
        "/api/v1/auth/login",
        json={"identifier": identifier, "password": password},
    )
    assert res.status_code == 200
    return res.json()["access_token"]


@pytest.mark.asyncio
async def test_admin_create_user_success():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        admin_token = await get_auth_token(client, "admin", "admin123")

        uid = uuid.uuid4().hex[:6]
        new_user_payload = {
            "name": f"DevOps Engineer {uid}",
            "username": f"devops_{uid}",
            "email": f"devops_{uid}@cortex.local",
            "password": "devops_password123",
            "job_title": "Site Reliability Engineer",
            "role": "Employee",
            "department": "Engineering",
            "clearance_level": "Internal",
        }

        res = await client.post(
            "/api/v1/users/",
            headers={"Authorization": f"Bearer {admin_token}"},
            json=new_user_payload,
        )
        assert res.status_code == 201
        data = res.json()
        assert data["username"] == f"devops_{uid}"
        assert data["department"] == "Engineering"
        assert data["role"] == "Employee"
        assert "hashed_password" not in data


@pytest.mark.asyncio
async def test_admin_create_user_duplicate_rejected():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        admin_token = await get_auth_token(client, "admin", "admin123")

        # Attempt to create user with existing username "admin"
        res = await client.post(
            "/api/v1/users/",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "name": "Duplicate Admin",
                "username": "admin",
                "email": "diff_email@cortex.local",
                "password": "password123",
                "job_title": "Admin",
                "role": "Admin",
                "department": "Engineering",
                "clearance_level": "Confidential",
            },
        )
        assert res.status_code == 409


@pytest.mark.asyncio
async def test_employee_cannot_create_user():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Non-admin employee logs in
        employee_token = await get_auth_token(client, "employee_eng", "employee123")

        res = await client.post(
            "/api/v1/users/",
            headers={"Authorization": f"Bearer {employee_token}"},
            json={
                "name": "Hacker",
                "username": "hacker",
                "email": "hacker@cortex.local",
                "password": "password123",
                "job_title": "Hacker",
                "role": "Admin",
                "department": "Engineering",
                "clearance_level": "Confidential",
            },
        )
        # Blocked: 403 Forbidden
        assert res.status_code == 403
        assert "Administrative privileges required" in res.json()["detail"]


@pytest.mark.asyncio
async def test_admin_list_users():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        admin_token = await get_auth_token(client, "admin", "admin123")

        res = await client.get(
            "/api/v1/users/",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert res.status_code == 200
        users_list = res.json()
        assert isinstance(users_list, list)
        assert len(users_list) >= 7  # 7 seeded users


@pytest.mark.asyncio
async def test_employee_cannot_list_users():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        employee_token = await get_auth_token(client, "employee_hr", "employee123")

        res = await client.get(
            "/api/v1/users/",
            headers={"Authorization": f"Bearer {employee_token}"},
        )
        assert res.status_code == 403


@pytest.mark.asyncio
async def test_admin_update_user():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        admin_token = await get_auth_token(client, "admin", "admin123")

        # 1. Fetch user to get ID
        users_res = await client.get(
            "/api/v1/users/",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        employee = next(u for u in users_res.json() if u["username"] == "employee_fin")
        user_id = employee["id"]

        # 2. Update clearance and job title
        update_res = await client.patch(
            f"/api/v1/users/{user_id}",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "job_title": "Senior Financial Controller",
                "clearance_level": "Confidential",
            },
        )
        assert update_res.status_code == 200
        updated = update_res.json()
        assert updated["job_title"] == "Senior Financial Controller"
        assert updated["clearance_level"] == "Confidential"

        # 3. Reset clearance back to Internal for test hygiene
        await client.patch(
            f"/api/v1/users/{user_id}",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "job_title": "Finance Analyst",
                "clearance_level": "Internal",
            },
        )


@pytest.mark.asyncio
async def test_admin_revoke_user_sessions():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        admin_token = await get_auth_token(client, "admin", "admin123")

        users_res = await client.get(
            "/api/v1/users/",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        target_user = next(u for u in users_res.json() if u["username"] == "manager_fin")
        target_id = target_user["id"]

        # Revoke sessions
        revoke_res = await client.post(
            f"/api/v1/users/{target_id}/revoke",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert revoke_res.status_code == 200
        assert revoke_res.json()["token_version"] == -1

        # Reset token_version back to 1 for test hygiene
        await client.patch(
            f"/api/v1/users/{target_id}",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"is_active": True},
        )
        from backend.database import async_session
        from backend.models.user import User
        from sqlmodel import select
        async with async_session() as session:
            stmt = select(User).where(User.id == uuid.UUID(target_id))
            res = await session.execute(stmt)
            u = res.scalars().first()
            if u:
                u.token_version = 1
                session.add(u)
                await session.commit()
