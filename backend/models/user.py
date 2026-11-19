from datetime import datetime, timezone
from typing import ClassVar, Optional
import uuid
from sqlmodel import Field, SQLModel

from backend.models.enums import ClearanceLevel, Role


class UserBase(SQLModel):
    name: str = Field(description="Display or full name of the user")
    username: str = Field(unique=True, index=True, description="Simple username for login (e.g. admin, manager_eng)")
    email: str = Field(unique=True, index=True, description="Email address for login and notifications")
    job_title: str = Field(description="Designation e.g. 'Senior AI Engineer', 'HR Director'")
    role: Role = Field(default=Role.EMPLOYEE, description="RBAC role: Admin, Manager, Employee")
    department: str = Field(index=True, description="User department e.g. Engineering, HR")
    clearance_level: ClearanceLevel = Field(default=ClearanceLevel.INTERNAL, description="Public, Internal, Confidential")
    is_active: bool = Field(default=True)


class User(UserBase, table=True):
    __tablename__: ClassVar[str] = "users"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    hashed_password: str
    token_version: int = Field(default=1, description="Incremented to invalidate all active JWT sessions")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class UserRead(UserBase):
    id: uuid.UUID
    must_change_password: bool = Field(default=False)
    created_at: datetime
    updated_at: datetime


class UserCreate(SQLModel):
    name: str
    username: str
    email: str
    password: Optional[str] = "Welcome@Cortex2026!"
    job_title: str
    role: Role = Role.EMPLOYEE
    department: str
    clearance_level: ClearanceLevel = ClearanceLevel.INTERNAL


class UserUpdate(SQLModel):
    name: Optional[str] = None
    username: Optional[str] = None
    email: Optional[str] = None
    job_title: Optional[str] = None
    role: Optional[Role] = None
    department: Optional[str] = None
    clearance_level: Optional[ClearanceLevel] = None
    is_active: Optional[bool] = None
    password: Optional[str] = Field(default=None, min_length=8, max_length=128)
