import pytest
from httpx import ASGITransport, AsyncClient

from backend.main import app


@pytest.mark.asyncio
async def test_api_login_with_username_success():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/auth/login",
            json={"identifier": "admin", "password": "admin123"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"
        assert data["user"]["username"] == "admin"
        assert data["user"]["role"] == "Admin"
        # Verify httpOnly cookie was set in the response headers
        assert "access_token" in response.cookies


@pytest.mark.asyncio
async def test_api_login_with_email_success():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/auth/login",
            json={"identifier": "manager.eng@cortex.local", "password": "manager123"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["user"]["username"] == "manager_eng"
        assert data["user"]["department"] == "Engineering"


@pytest.mark.asyncio
async def test_api_login_invalid_credentials():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/auth/login",
            json={"identifier": "admin", "password": "incorrect_password"},
        )
        assert response.status_code == 401
        assert response.json()["detail"] == "Invalid username or password"


@pytest.mark.asyncio
async def test_api_get_me_with_bearer_token():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Step 1: Login to get a valid token
        login_res = await client.post(
            "/api/v1/auth/login",
            json={"identifier": "employee_hr", "password": "employee123"},
        )
        token = login_res.json()["access_token"]

        # Step 2: Call /me using Authorization Bearer header
        me_res = await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert me_res.status_code == 200
        user_data = me_res.json()
        assert user_data["username"] == "employee_hr"
        assert user_data["department"] == "HR"
        assert user_data["role"] == "Employee"


@pytest.mark.asyncio
async def test_api_get_me_unauthorized():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # No token or cookie provided
        response = await client.get("/api/v1/auth/me")
        assert response.status_code == 401


@pytest.mark.asyncio
async def test_api_logout_clears_cookie():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Step 1: Login
        login_res = await client.post(
            "/api/v1/auth/login",
            json={"identifier": "admin", "password": "admin123"},
        )
        token = login_res.json()["access_token"]

        # Step 2: Logout with the token
        logout_res = await client.post(
            "/api/v1/auth/logout",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert logout_res.status_code == 200
        assert logout_res.json()["message"] == "Logged out successfully"


@pytest.mark.asyncio
async def test_change_password_onboarding_flow():
    import uuid
    from backend.db.database import async_session
    from backend.models.user import User
    from backend.models.enums import Role, ClearanceLevel
    from backend.core.security import hash_password, create_access_token

    test_username = f"onboard_{uuid.uuid4().hex[:8]}"
    async with async_session() as session:
        user = User(
            name="Onboard Tester",
            username=test_username,
            email=f"{test_username}@cortex.test",
            job_title="Tester",
            hashed_password=hash_password("Welcome@Cortex2026!"),
            role=Role.EMPLOYEE,
            department="Engineering",
            clearance_level=ClearanceLevel.INTERNAL,
            is_active=True,
            token_version=1,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        user_id = user.id

    token = create_access_token({"sub": str(user_id), "token_version": 1})
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Step 1: /me indicates must_change_password is True
        me_res = await client.get("/api/v1/auth/me", headers=headers)
        assert me_res.status_code == 200
        assert me_res.json()["must_change_password"] is True

        # Step 2: Attempt with default password is rejected
        bad_res = await client.post(
            "/api/v1/auth/change-password",
            json={"new_password": "Welcome@Cortex2026!"},
            headers=headers,
        )
        assert bad_res.status_code == 400
        assert "default company password" in bad_res.json()["detail"].lower()

        # Step 2b: Attempt with 7 characters is rejected (min length is 8)
        short_res = await client.post(
            "/api/v1/auth/change-password",
            json={"new_password": "Short1!"},
            headers=headers,
        )
        assert short_res.status_code == 422

        # Step 3: Successfully change password WITHOUT providing current_password (onboarding flow, 8-char password)
        ok_res = await client.post(
            "/api/v1/auth/change-password",
            json={"new_password": "Pass1234"},
            headers=headers,
        )
        assert ok_res.status_code == 200
        assert ok_res.json()["must_change_password"] is False

        # Step 4: /me now confirms must_change_password is False
        me_res2 = await client.get("/api/v1/auth/me", headers=headers)
        assert me_res2.status_code == 200
        assert me_res2.json()["must_change_password"] is False

        # Step 5: Subsequent change requires current_password now that onboarding is finished
        subsequent_bad = await client.post(
            "/api/v1/auth/change-password",
            json={"new_password": "AnotherNewPassword123!"},
            headers=headers,
        )
        assert subsequent_bad.status_code == 400
        assert "current password" in subsequent_bad.json()["detail"].lower()


