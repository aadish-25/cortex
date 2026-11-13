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
