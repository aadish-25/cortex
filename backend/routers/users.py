from datetime import datetime, timezone
import re
from typing import List, Optional
import uuid
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import case, func
from sqlmodel import and_, col, or_, select

from backend.core.security import get_current_user, hash_password, require_user_manager
from backend.db.database import get_db
from backend.models.enums import AuditCategory, Role
from backend.models.user import User, UserCreate, UserRead, UserUpdate
from backend.models.email_request import EmailChangeRequest
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

    # 2. Hash standard default organization password securely (enforcing company onboarding password)
    # Administrators are not allowed to choose custom passwords for employees; standard default is strictly enforced
    hashed_pwd = hash_password("Welcome@Cortex2026!")

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
    elif account_status == "email_pending":
        email_sub = select(EmailChangeRequest.user_id).where(EmailChangeRequest.status == "pending")
        conditions.append(User.id.in_(email_sub))
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

    # Prioritize users requiring administrator action/review at the top of directory (Page 1)
    pending_email_sub = (
        select(func.count(EmailChangeRequest.id))
        .where(
            EmailChangeRequest.user_id == User.id,
            EmailChangeRequest.status == "pending",
        )
        .scalar_subquery()
    )
    is_pending_applicant = and_(
        User.is_active == False,
        or_(User.department == "Unassigned", User.job_title == "Pending Assignment"),
    )
    has_pending_review = case(
        (pending_email_sub > 0, 1),
        (is_pending_applicant, 1),
        else_=0,
    )

    data_stmt = (
        data_stmt.order_by(
            has_pending_review.desc(),
            col(User.created_at).desc(),
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
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
            # Allow provisioning default password only when activating a new employee applicant
            is_activating_applicant = not user.is_active and payload.is_active is True
            if not is_activating_applicant:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Administrators cannot modify an employee's personal password. Employees must change their own password.",
                )
            # Strictly enforce standard company default password during activation
            user.hashed_password = hash_password("Welcome@Cortex2026!")
        else:
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


from pydantic import BaseModel, Field


class RequestEmailChangePayload(BaseModel):
    requested_email: str = Field(min_length=5, max_length=254)
    reason: Optional[str] = Field(default=None, max_length=500)


@router.post("/request-email-change", status_code=status.HTTP_201_CREATED)
async def request_email_change(
    payload: RequestEmailChangePayload,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    """
    Submit an official request to update assigned corporate email.
    Reviewed by administrators. Enforces strictly once-in-a-lifetime limit.
    """
    clean_email = payload.requested_email.strip().lower()
    if not clean_email.endswith("@cortex.com"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Company email must end with @cortex.com",
        )

    local_part = clean_email[:-11]
    if not local_part or not re.match(r"^[a-z0-9._-]+$", local_part):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid email prefix. Use letters, numbers, dots, or hyphens before @cortex.com",
        )

    if clean_email == current_user.email.lower():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Requested email is already your current company email.",
        )

    # Strictly once in lifetime check
    existing_any = await session.execute(
        select(EmailChangeRequest).where(EmailChangeRequest.user_id == current_user.id)
    )
    if existing_any.scalars().first():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Company email can only be revised once in a lifetime. You have already submitted a revision request.",
        )

    dup = await session.execute(select(User).where(User.email == clean_email))
    if dup.scalars().first():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This email address is already assigned to another user.",
        )

    req = EmailChangeRequest(
        user_id=current_user.id,
        user_name=current_user.name,
        current_email=current_user.email,
        requested_email=clean_email,
        reason=payload.reason.strip() if payload.reason else None,
        status="pending",
    )
    session.add(req)
    await session.commit()
    await session.refresh(req)

    await log_audit_event(
        session=session,
        event_tag="USER_EMAIL_CHANGE_REQUESTED",
        category=AuditCategory.AUTH,
        user_id=current_user.id,
        status_code=201,
        details=f"current={current_user.email}; requested={clean_email}",
    )

    return {
        "message": "Email revision request submitted. An administrator will review your request.",
        "request_id": str(req.id),
        "requested_email": req.requested_email,
    }


@router.get("/my-email-requests")
async def get_my_email_requests(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    """List the current user's email revision requests."""
    stmt = (
        select(EmailChangeRequest)
        .where(EmailChangeRequest.user_id == current_user.id)
        .order_by(col(EmailChangeRequest.created_at).desc())
    )
    res = await session.execute(stmt)
    return res.scalars().all()
