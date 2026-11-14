from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.config import settings
from backend.core.security import get_current_user
from backend.db.database import get_db
from backend.models.enums import AuditCategory
from backend.models.user import User, UserRead
from backend.services.audit_service import log_audit_event
from backend.services.auth_service import authenticate_user

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class LoginRequest(BaseModel):
    identifier: str  # Simple username (e.g. "admin") OR email (e.g. "admin@cortex.local")
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserRead


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
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        samesite="lax",
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
    response.delete_cookie(key="access_token")
    await log_audit_event(
        session=session,
        event_tag="AUTH_LOGOUT",
        category=AuditCategory.AUTH,
        user_id=current_user.id,
        status_code=200,
        details="User logged out",
    )
    return {"message": "Logged out successfully"}
