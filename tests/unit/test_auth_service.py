import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from backend.models.audit import AuditLog
from backend.models.enums import Role
from backend.services.auth.auth_service import authenticate_user, revoke_user_sessions

# Note: The `db_session` fixture parameter used across these tests is automatically
# discovered and injected by pytest from tests/conftest.py (no manual import needed).


@pytest.mark.asyncio
async def test_login_with_username(db_session: AsyncSession):
    user, token = await authenticate_user(db_session, "admin", "admin123")
    assert user is not None
    assert user.username == "admin"
    assert user.role == Role.ADMIN
    assert isinstance(token, str)
    assert len(token) > 0


@pytest.mark.asyncio
async def test_login_with_email(db_session: AsyncSession):
    user, token = await authenticate_user(db_session, "employee.fin@cortex.local", "employee123")
    assert user is not None
    assert user.username == "employee_fin"
    assert user.department == "Finance"
    assert user.role == Role.EMPLOYEE
    assert isinstance(token, str)


@pytest.mark.asyncio
async def test_login_wrong_password(db_session: AsyncSession):
    with pytest.raises(HTTPException) as exc_info:
        await authenticate_user(db_session, "admin", "wrong_password_123")
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Invalid username or password"


@pytest.mark.asyncio
async def test_login_nonexistent_user(db_session: AsyncSession):
    with pytest.raises(HTTPException) as exc_info:
        await authenticate_user(db_session, "nonexistent_user", "password123")
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Invalid username or password"


@pytest.mark.asyncio
async def test_token_version_revocation(db_session: AsyncSession):
    user, _ = await authenticate_user(db_session, "manager_eng", "manager123")

    updated_user = await revoke_user_sessions(db_session, user.id)
    assert updated_user.token_version == -1

    # Restore version back to 1 for database consistency
    updated_user.token_version = 1
    db_session.add(updated_user)
    await db_session.commit()


@pytest.mark.asyncio
async def test_audit_log_created_on_failed_login(db_session: AsyncSession):
    with pytest.raises(HTTPException):
        await authenticate_user(db_session, "admin", "incorrect_secret")

    stmt = select(AuditLog).where(AuditLog.event_tag == "AUTH_LOGIN_FAILED")
    result = await db_session.execute(stmt)
    failed_log = result.scalars().first()
    assert failed_log is not None
    assert failed_log.status_code == 401
    # Verify zero-PII: typed password is not in details
    assert "incorrect_secret" not in (failed_log.details or "")
