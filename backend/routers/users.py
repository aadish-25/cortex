from datetime import datetime, timezone
from typing import List, Optional
import uuid
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from backend.core.security import hash_password, require_user_manager
from backend.db.database import get_db
from backend.models.enums import AuditCategory
from backend.models.user import User, UserCreate, UserRead, UserUpdate
from backend.services.audit_service import log_audit_event
from backend.services.auth_service import revoke_user_sessions

router = APIRouter(prefix="/api/v1/users", tags=["users"])


@router.post("/", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def create_user(
    payload: UserCreate,
    admin_user: User = Depends(require_user_manager),
    session: AsyncSession = Depends(get_db),
):
    """
    Administrative endpoint to provision a new user (D23).
    Restricted to authorized administrator roles.
    """
    # 1. Prevent duplicate usernames or emails
    existing_stmt = select(User).where(
        (User.username == payload.username) | (User.email == payload.email)
    )
    res = await session.execute(existing_stmt)
    if res.scalars().first():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A user with this username or email already exists",
        )

    # 2. Hash password securely
    hashed_pwd = hash_password(payload.password)

    # 3. Create user entity
    new_user = User(
        name=payload.name,
        username=payload.username,
        email=payload.email,
        job_title=payload.job_title,
        hashed_password=hashed_pwd,
        role=payload.role,
        department=payload.department,
        clearance_level=payload.clearance_level,
        token_version=1,
        is_active=True,
    )
    session.add(new_user)
    await session.commit()
    await session.refresh(new_user)

    # 4. Record audit event (Zero-PII)
    await log_audit_event(
        session=session,
        event_tag="USER_CREATED",
        category=AuditCategory.ADMIN,
        user_id=admin_user.id,
        status_code=201,
        details=f"Admin created user: {new_user.username} ({new_user.role.value}/{new_user.department})",
    )

    return UserRead.model_validate(new_user)


@router.get("/", response_model=List[UserRead])
async def list_users(
    admin_user: User = Depends(require_user_manager),
    session: AsyncSession = Depends(get_db),
):
    """
    List all users in the organization for the Admin Management Dashboard.
    """
    stmt = select(User).order_by(col(User.created_at).desc())
    res = await session.execute(stmt)
    users = res.scalars().all()
    return [UserRead.model_validate(u) for u in users]


@router.get("/{user_id}", response_model=UserRead)
async def get_user_by_id(
    user_id: uuid.UUID,
    admin_user: User = Depends(require_user_manager),
    session: AsyncSession = Depends(get_db),
):
    """
    Retrieve user details by user ID.
    """
    stmt = select(User).where(User.id == user_id)
    res = await session.execute(stmt)
    user = res.scalars().first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    return UserRead.model_validate(user)


@router.patch("/{user_id}", response_model=UserRead)
async def update_user(
    user_id: uuid.UUID,
    payload: UserUpdate,
    admin_user: User = Depends(require_user_manager),
    session: AsyncSession = Depends(get_db),
):
    """
    Update a user's role, clearance, department, or active status (D23, FRONTEND_SPECS).
    """
    stmt = select(User).where(User.id == user_id)
    res = await session.execute(stmt)
    user = res.scalars().first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # Check for username / email collisions if updated
    if payload.username and payload.username != user.username:
        dup_stmt = select(User).where(User.username == payload.username)
        dup_res = await session.execute(dup_stmt)
        if dup_res.scalars().first():
            raise HTTPException(status_code=409, detail="Username already in use")
        user.username = payload.username

    if payload.email and payload.email != user.email:
        dup_stmt = select(User).where(User.email == payload.email)
        dup_res = await session.execute(dup_stmt)
        if dup_res.scalars().first():
            raise HTTPException(status_code=409, detail="Email already in use")
        user.email = payload.email

    # Apply updates
    if payload.name is not None:
        user.name = payload.name
    if payload.job_title is not None:
        user.job_title = payload.job_title
    if payload.role is not None:
        user.role = payload.role
    if payload.department is not None:
        user.department = payload.department
    if payload.clearance_level is not None:
        user.clearance_level = payload.clearance_level

    # If deactivating user, invalidate active sessions immediately (FRONTEND_SPECS)
    if payload.is_active is not None:
        user.is_active = payload.is_active
        if not payload.is_active:
            user.token_version = -1

    user.updated_at = datetime.now(timezone.utc)
    session.add(user)
    await session.commit()
    await session.refresh(user)

    await log_audit_event(
        session=session,
        event_tag="USER_UPDATED",
        category=AuditCategory.ADMIN,
        user_id=admin_user.id,
        status_code=200,
        details=f"Admin updated user {user.username} (active={user.is_active})",
    )

    return UserRead.model_validate(user)


@router.post("/{user_id}/revoke")
async def revoke_sessions(
    user_id: uuid.UUID,
    admin_user: User = Depends(require_user_manager),
    session: AsyncSession = Depends(get_db),
):
    """
    Instant session termination for a user (D18). Sets token_version to -1.
    """
    updated_user = await revoke_user_sessions(
        session=session,
        user_id=user_id,
        revoked_by=admin_user.id,
    )
    return {
        "message": f"All sessions revoked for user {updated_user.username}",
        "token_version": updated_user.token_version,
    }
