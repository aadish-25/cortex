import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional
import uuid
import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from backend.core.config import settings
from backend.db.database import get_db
from backend.models.enums import Role
from backend.models.user import User

bearer_scheme = HTTPBearer(auto_error=False)


def truncate_password(password: str) -> bytes:
    """Truncate to 72 bytes for bcrypt specification compliance (D20)."""
    return password.encode("utf-8")[:72]


def hash_password(password: str) -> str:
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(truncate_password(password), salt)
    return hashed.decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(
        truncate_password(plain_password),
        hashed_password.encode("utf-8"),
    )


DEFAULT_ORGANIZATION_PASSWORD = "Welcome@Cortex2026!"
DEFAULT_ORGANIZATION_PASSWORDS = {DEFAULT_ORGANIZATION_PASSWORD}

# Thread-safe in-memory cache of default password verification results.
# Key: hashed_password (str), Value: is_default (bool)
_DEFAULT_PASSWORD_CACHE: dict[str, bool] = {}


def record_password_default_status(hashed_password: str, is_default: bool) -> None:
    """Explicitly cache whether a password hash corresponds to the default company password."""
    if hashed_password:
        _DEFAULT_PASSWORD_CACHE[hashed_password] = is_default


def is_default_password(hashed_password: str) -> bool:
    """
    Return True if the user's password matches the company default password ('Welcome@Cortex2026!').
    Uses in-memory cache when available to eliminate redundant bcrypt CPU overhead.
    """
    if not hashed_password:
        return False
    cached = _DEFAULT_PASSWORD_CACHE.get(hashed_password)
    if cached is not None:
        return cached

    try:
        is_default = verify_password(DEFAULT_ORGANIZATION_PASSWORD, hashed_password)
    except Exception:
        is_default = False

    _DEFAULT_PASSWORD_CACHE[hashed_password] = is_default
    return is_default


async def is_default_password_async(hashed_password: str) -> bool:
    """
    Asynchronously verify default password status. If uncached, executes bcrypt in a worker
    thread via asyncio.to_thread so it never blocks the FastAPI main asyncio event loop.
    """
    if not hashed_password:
        return False
    cached = _DEFAULT_PASSWORD_CACHE.get(hashed_password)
    if cached is not None:
        return cached
    return await asyncio.to_thread(is_default_password, hashed_password)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(
            minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
        )
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(
        to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM
    )
    return encoded_jwt


async def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    session: AsyncSession = Depends(get_db),
) -> User:
    token: Optional[str] = None
    if credentials:
        token = credentials.credentials
    elif "access_token" in request.cookies:
        token = request.cookies["access_token"]

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
        )
        user_id_raw = payload.get("sub")
        token_version_raw = payload.get("token_version")

        if not isinstance(user_id_raw, str) or not isinstance(token_version_raw, int):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token payload",
            )
        user_id = uuid.UUID(user_id_raw)
        token_version = token_version_raw
    except (jwt.PyJWTError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    statement = select(User).where(User.id == user_id)
    result = await session.execute(statement)
    user = result.scalars().first()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )

    if not user.is_active or user.token_version == -1:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User account deactivated",
        )

    # Token Version Revocation check (D18)
    if user.token_version != token_version:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session has been revoked",
        )

    return user


# Modular role sets for administrative operations (D23)
# Can add other roles later who can to user management such as create=ing an user and assigning them roles and permissions
USER_MANAGEMENT_ROLES = {Role.ADMIN}
DOCUMENT_MANAGEMENT_ROLES = {Role.ADMIN, Role.MANAGER}


def require_roles(*allowed_roles: Role):
    async def role_checker(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        return current_user

    return role_checker


async def require_user_manager(current_user: User = Depends(get_current_user)) -> User:
    """Modular guard: checks if user has administrative provisioning privileges (D23)."""
    if current_user.role not in USER_MANAGEMENT_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrative privileges required to manage users",
        )
    return current_user


async def require_document_manager(current_user: User = Depends(get_current_user)) -> User:
    """Modular guard: checks if user has document management privileges (Admin or Manager)."""
    if current_user.role not in DOCUMENT_MANAGEMENT_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrative or Managerial privileges required to manage documents",
        )
    return current_user
