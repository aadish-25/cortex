from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.config import settings
from backend.core.security import get_current_user
from backend.db.database import get_db
from backend.models.enums import AuditCategory
from backend.models.user import User, UserRead
from backend.services.auth.audit_service import log_audit_event
from backend.services.auth.auth_service import authenticate_user
from backend.core.security import hash_password
from backend.models.enums import ClearanceLevel, Role
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

    return TokenResponse(
        access_token=token,
        token_type="bearer",
        user=UserRead.model_validate(user),
    )


@router.get("/me", response_model=UserRead)
async def get_me(current_user: User = Depends(get_current_user)):
    """
    Retrieve current authenticated user's profile information.
    """
    return UserRead.model_validate(current_user)


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
