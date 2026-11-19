from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.config import settings
from backend.core.security import (
    DEFAULT_ORGANIZATION_PASSWORDS,
    get_current_user,
    hash_password,
    is_default_password,
    verify_password,
)
from backend.db.database import get_db
from backend.models.enums import AuditCategory, ClearanceLevel, Role
from backend.models.user import User, UserRead
from backend.services.auth.audit_service import log_audit_event
from backend.services.auth.auth_service import authenticate_user
from sqlmodel import select

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class LoginRequest(BaseModel):
    identifier: str  # Simple username (e.g. "admin") OR email (e.g. "admin@cortex.local")
    password: str


import re
import secrets
from typing import Optional


class RegisterRequest(BaseModel):
    name: Optional[str] = Field(default=None, max_length=120)
    first_name: Optional[str] = Field(default=None, min_length=1, max_length=60)
    last_name: Optional[str] = Field(default=None, min_length=1, max_length=60)
    email: str = Field(min_length=5, max_length=254)
    username: Optional[str] = Field(default=None, min_length=3, max_length=60, pattern=r"^[A-Za-z0-9_.-]+$")
    password: Optional[str] = Field(default=None, min_length=10, max_length=128)
    job_title: Optional[str] = Field(default=None, min_length=2, max_length=120)
    department: Optional[str] = Field(default=None, min_length=2, max_length=80)


class RegistrationResponse(BaseModel):
    status: str
    message: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserRead


@router.post("/register", response_model=RegistrationResponse, status_code=status.HTTP_202_ACCEPTED)
async def register_account(
    payload: RegisterRequest,
    session: AsyncSession = Depends(get_db),
):
    """Create an inactive employee account request for administrator approval.

    Public registration never grants a role or clearance and cannot access company
    data until an administrator activates the account and assigns corporate details.
    """
    identifier = payload.email.strip().lower()
    if payload.username:
        username = payload.username.strip()
    else:
        local_part = re.sub(r"[^A-Za-z0-9_.-]+", "_", identifier.split("@")[0]).strip("._-") or "user"
        username = f"{local_part[:48]}_{secrets.token_hex(3)}"

    existing = await session.execute(
        select(User).where((User.username == username) | (User.email == identifier))
    )
    if existing.scalars().first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="An account with that username or email already exists")

    raw_password = payload.password or secrets.token_urlsafe(24)
    if payload.first_name and payload.last_name:
        display_name = f"{payload.first_name.strip()} {payload.last_name.strip()}"
    elif payload.name:
        display_name = payload.name.strip()
    elif payload.first_name:
        display_name = payload.first_name.strip()
    else:
        display_name = "New Employee"

    user = User(
        name=display_name,
        username=username,
        email=identifier,
        job_title=(payload.job_title or "Pending Assignment").strip(),
        hashed_password=hash_password(raw_password),
        role=Role.EMPLOYEE,
        department=(payload.department or "Unassigned").strip(),
        clearance_level=ClearanceLevel.PUBLIC,
        is_active=False,
        token_version=-1,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    await log_audit_event(
        session=session,
        event_tag="AUTH_REGISTRATION_PENDING",
        category=AuditCategory.AUTH,
        user_id=user.id,
        status_code=202,
        details=f"target_user_id={user.id}; approval_required=true",
    )
    return RegistrationResponse(
        status="pending_approval",
        message="Account request submitted. An administrator must activate it before you can sign in.",
    )


@router.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest,
    response: Response,
    session: AsyncSession = Depends(get_db),
):
    """
    Authenticate user via username or email, issue JWT, and set secure httpOnly cookie.
    """
    user, token = await authenticate_user(
        session=session,
        identifier=payload.identifier,
        password=payload.password,
    )

    # Set httpOnly cookie for web browser clients
    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        secure=settings.ENVIRONMENT.lower() not in {"development", "dev", "test"},
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        samesite="lax",
        path="/",
    )

    user_read = UserRead.model_validate(user)
    user_read.must_change_password = is_default_password(user.hashed_password)

    return TokenResponse(
        access_token=token,
        token_type="bearer",
        user=user_read,
    )


@router.get("/me", response_model=UserRead)
async def get_me(current_user: User = Depends(get_current_user)):
    """
    Retrieve current authenticated user's profile information.
    """
    user_read = UserRead.model_validate(current_user)
    user_read.must_change_password = is_default_password(current_user.hashed_password)
    return user_read


class ChangePasswordRequest(BaseModel):
    current_password: Optional[str] = None
    new_password: str = Field(min_length=10, max_length=128)


@router.post("/change-password")
async def change_password(
    payload: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    """
    Allow an authenticated user to change their password.
    Blocks keeping or reusing the standard organization default password.
    If the user currently has a default organization password (onboarding),
    current_password is not required.
    """
    is_onboarding = is_default_password(current_user.hashed_password)

    if not is_onboarding:
        if not payload.current_password or not verify_password(payload.current_password, current_user.hashed_password):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Current password is required and must be correct.",
            )

    cleaned_new = payload.new_password.strip()
    if cleaned_new.lower() in {p.lower() for p in DEFAULT_ORGANIZATION_PASSWORDS}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This password is not allowed. You cannot keep or reuse the default company password. Please choose a unique personal password.",
        )

    if verify_password(cleaned_new, current_user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Your new password cannot be the same as your current default password.",
        )

    current_user.hashed_password = hash_password(cleaned_new)
    from datetime import datetime, timezone
    current_user.updated_at = datetime.now(timezone.utc)
    session.add(current_user)
    await session.commit()
    await session.refresh(current_user)

    await log_audit_event(
        session=session,
        event_tag="AUTH_PASSWORD_CHANGED",
        category=AuditCategory.AUTH,
        user_id=current_user.id,
        status_code=200,
        details="User updated personal password",
    )

    return {"message": "Password changed successfully.", "must_change_password": False}


@router.post("/logout")
async def logout(
    response: Response,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    """
    Clear access_token cookie and record logout audit event.
    """
    response.delete_cookie(key="access_token", path="/", httponly=True, secure=settings.ENVIRONMENT.lower() not in {"development", "dev", "test"}, samesite="lax")
    await log_audit_event(
        session=session,
        event_tag="AUTH_LOGOUT",
        category=AuditCategory.AUTH,
        user_id=current_user.id,
        status_code=200,
        details="User logged out",
    )
    return {"message": "Logged out successfully"}
