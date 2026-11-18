from datetime import datetime, timezone
import re
from typing import List, Optional
import uuid
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func
from sqlmodel import col, or_, select

from backend.core.security import hash_password, require_user_manager
from backend.db.database import get_db
from backend.models.enums import AuditCategory, Role
from backend.models.user import User, UserCreate, UserRead, UserUpdate
from backend.models.pagination import PaginatedResponse
from backend.services.auth.audit_service import log_audit_event
from backend.services.auth.auth_service import revoke_user_sessions

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
        details=f"target_user_id={new_user.id}; role={new_user.role.value}; department={new_user.department}",
    )

    return UserRead.model_validate(new_user)


@router.get("/", response_model=PaginatedResponse[UserRead])
async def list_users(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    search: Optional[str] = Query(default=None, max_length=120),
    department: Optional[str] = None,
    role: Optional[str] = None,
    is_active: Optional[bool] = None,
    account_status: Optional[str] = Query(default=None),
    admin_user: User = Depends(require_user_manager),
    session: AsyncSession = Depends(get_db),
):
    """Paginated organization user directory for administrators."""
    conditions = []
    if search:
        term = f"%{search.strip()}%"
        conditions.append(or_(User.name.ilike(term), User.username.ilike(term), User.email.ilike(term)))
    if department:
        conditions.append(User.department == department)
    if role:
        try:
            conditions.append(User.role == Role(role))
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid role filter")
    if account_status == "active":
        conditions.append(User.is_active == True)
    elif account_status == "pending":
        conditions.append(User.is_active == False)
        conditions.append(or_(User.department == "Unassigned", User.job_title == "Pending Assignment"))
    elif account_status == "inactive":
        conditions.append(User.is_active == False)
        conditions.append(User.department != "Unassigned")
        conditions.append(User.job_title != "Pending Assignment")
    elif is_active is not None:
        conditions.append(User.is_active == is_active)

    count_stmt = select(func.count(User.id))
    data_stmt = select(User)
    if conditions:
        count_stmt = count_stmt.where(*conditions)
        data_stmt = data_stmt.where(*conditions)
    total = int((await session.execute(count_stmt)).scalar_one() or 0)
    data_stmt = data_stmt.order_by(col(User.created_at).desc()).offset((page - 1) * page_size).limit(page_size)
    result = await session.execute(data_stmt)
    users = result.scalars().all()
    return PaginatedResponse[UserRead](
        items=[UserRead.model_validate(u) for u in users],
        page=page,
        page_size=page_size,
        total=total,
        total_pages=(total + page_size - 1) // page_size,
    )

@router.get("/suggest-company-email")
async def suggest_company_email(
    first_name: str = Query(..., min_length=1),
    last_name: str = Query(default="", max_length=60),
    exclude_user_id: Optional[uuid.UUID] = Query(default=None),
    admin_user: User = Depends(require_user_manager),
    session: AsyncSession = Depends(get_db),
):
    """
    Generate a corporate email (firstname.lastname@cortex.com) handling collisions.
    """
    clean_first = re.sub(r"[^a-z0-9]", "", first_name.strip().lower()) or "user"
    clean_last = re.sub(r"[^a-z0-9]", "", last_name.strip().lower())
    base = f"{clean_first}.{clean_last}" if clean_last else clean_first

    candidate = f"{base}@cortex.com"
    counter = 1
    while True:
        stmt = select(User.id).where(User.email == candidate)
        if exclude_user_id:
            stmt = stmt.where(User.id != exclude_user_id)
        res = await session.execute(stmt)
        if not res.scalars().first():
            break
        candidate = f"{base}{counter}@cortex.com"
        counter += 1

    username_candidate = candidate.split("@")[0]
    u_counter = 1
    u_candidate = username_candidate
    while True:
        u_stmt = select(User.id).where(User.username == u_candidate)
        if exclude_user_id:
            u_stmt = u_stmt.where(User.id != exclude_user_id)
        res_u = await session.execute(u_stmt)
        if not res_u.scalars().first():
            break
        u_candidate = f"{username_candidate}{u_counter}"
        u_counter += 1

    return {"suggested_email": candidate, "suggested_username": u_candidate}


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
    if payload.password:
        if admin_user.id != user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Administrators cannot modify an employee's personal password. Employees must change their own password.",
            )
        cleaned = payload.password.strip()
        from backend.core.security import DEFAULT_ORGANIZATION_PASSWORDS
        if cleaned.lower() in {p.lower() for p in DEFAULT_ORGANIZATION_PASSWORDS}:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="This password is not allowed. You cannot keep or reuse the default company password.",
            )
        user.hashed_password = hash_password(cleaned)
        user.token_version = max(1, user.token_version) + 1

    # If deactivating user, invalidate active sessions immediately (FRONTEND_SPECS)
    if payload.is_active is not None and payload.is_active != user.is_active:
        # Monotonically advance the version on both deactivate and reactivate.
        # Reusing version 1 after a -1 sentinel would revive old JWTs.
        user.token_version = max(1, user.token_version) + 1
        user.is_active = payload.is_active

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
        details=f"target_user_id={user.id}; active={user.is_active}; role={user.role.value}; department={user.department}",
    )

    return UserRead.model_validate(user)


@router.post("/{user_id}/revoke")
async def revoke_sessions(
    user_id: uuid.UUID,
    admin_user: User = Depends(require_user_manager),
    session: AsyncSession = Depends(get_db),
):
    """
    Instant session termination for a user (D18). Advances token_version to invalidate existing JWTs.
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


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: uuid.UUID,
    admin_user: User = Depends(require_user_manager),
    session: AsyncSession = Depends(get_db),
):
    """
    Remove or reject a user account request.
    """
    stmt = select(User).where(User.id == user_id)
    res = await session.execute(stmt)
    user = res.scalars().first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    await session.delete(user)
    await session.commit()
    await log_audit_event(
        session=session,
        event_tag="USER_DELETED",
        category=AuditCategory.ADMIN,
        user_id=admin_user.id,
        status_code=204,
        details=f"target_user_id={user_id}; name={user.name}; email={user.email}",
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
