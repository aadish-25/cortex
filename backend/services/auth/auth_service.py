import uuid
from typing import Tuple, Optional
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from backend.models.enums import AuditCategory
from backend.models.user import User
from backend.core.security import create_access_token, verify_password
from backend.services.auth.audit_service import log_audit_event


async def authenticate_user(
    session: AsyncSession,
    identifier: str,
    password: str,
) -> Tuple[User, str]:
    """
    Authenticate a user by either username or email.
    Returns (user, access_token) on success.
    """
    # 1. Look up user by username OR email
    statement = select(User).where(
        (User.username == identifier) | (User.email == identifier)
    )
    result = await session.execute(statement)
    user = result.scalars().first()

    # 2. Check credentials (constant time verification via bcrypt)
    if not user or not verify_password(password, user.hashed_password):
        await log_audit_event(
            session=session,
            event_tag="AUTH_LOGIN_FAILED",
            category=AuditCategory.AUTH,
            user_id=user.id if user else None,
            status_code=401,
            details=f"Failed login attempt for identifier: {identifier}",
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )

    # 3. Verify account state (active & not revoked)
    if not user.is_active or user.token_version == -1:
        await log_audit_event(
            session=session,
            event_tag="AUTH_LOGIN_BLOCKED",
            category=AuditCategory.AUTH,
            user_id=user.id,
            status_code=403,
            details="Login blocked: account inactive or revoked",
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is inactive or revoked",
        )

    # 4. Issue JWT with embedded token_version for instant revocation (D18)
    token_data = {
        "sub": str(user.id),
        "token_version": user.token_version,
        "role": user.role.value,
    }
    access_token = create_access_token(data=token_data)

    # 5. Log successful authentication
    await log_audit_event(
        session=session,
        event_tag="AUTH_LOGIN_SUCCESS",
        category=AuditCategory.AUTH,
        user_id=user.id,
        status_code=200,
        details="User authenticated successfully",
    )

    return user, access_token


async def revoke_user_sessions(
    session: AsyncSession,
    user_id: uuid.UUID,
    revoked_by: Optional[uuid.UUID] = None,
) -> User:
    """
    Instantly revoke all active JWT tokens for a user by setting token_version = -1 (D18).
    """
    statement = select(User).where(User.id == user_id)
    result = await session.execute(statement)
    user = result.scalars().first()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # Set token_version to -1 per D18 specification
    user.token_version = -1
    session.add(user)
    await session.commit()
    await session.refresh(user)

    await log_audit_event(
        session=session,
        event_tag="AUTH_SESSIONS_REVOKED",
        category=AuditCategory.ADMIN,
        user_id=revoked_by or user.id,
        status_code=200,
        details=f"Admin {revoked_by} revoked sessions for user {user.username} (token_version set to -1)",
    )

    return user
