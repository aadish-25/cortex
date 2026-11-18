import asyncio
import os
import sys
import secrets

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from backend.db.database import async_session, init_db
from backend.models.enums import ClearanceLevel, Role
from backend.models.user import User
from backend.core.security import hash_password
from backend.core.config import settings

INITIAL_USERS = [
    {
        "name": "Admin",
        "username": "admin",
        "email": "admin@cortex.local",
        "password": "",
        "job_title": "System Administrator",
        "role": Role.ADMIN,
        "department": "Enterprise",
        "clearance_level": ClearanceLevel.CONFIDENTIAL,
    },
    {
        "name": "Manager (Eng)",
        "username": "manager_eng",
        "email": "manager.eng@cortex.local",
        "password": "manager123",
        "job_title": "Engineering Lead",
        "role": Role.MANAGER,
        "department": "Engineering",
        "clearance_level": ClearanceLevel.CONFIDENTIAL,
    },
    {
        "name": "Employee (Eng)",
        "username": "employee_eng",
        "email": "employee.eng@cortex.local",
        "password": "employee123",
        "job_title": "Software Engineer",
        "role": Role.EMPLOYEE,
        "department": "Engineering",
        "clearance_level": ClearanceLevel.INTERNAL,
    },
    {
        "name": "Manager (HR)",
        "username": "manager_hr",
        "email": "manager.hr@cortex.local",
        "password": "manager123",
        "job_title": "HR Director",
        "role": Role.MANAGER,
        "department": "HR",
        "clearance_level": ClearanceLevel.CONFIDENTIAL,
    },
    {
        "name": "Employee (HR)",
        "username": "employee_hr",
        "email": "employee.hr@cortex.local",
        "password": "employee123",
        "job_title": "HR Associate",
        "role": Role.EMPLOYEE,
        "department": "HR",
        "clearance_level": ClearanceLevel.INTERNAL,
    },
    {
        "name": "Manager (Finance)",
        "username": "manager_fin",
        "email": "manager.fin@cortex.local",
        "password": "manager123",
        "job_title": "Finance Lead",
        "role": Role.MANAGER,
        "department": "Finance",
        "clearance_level": ClearanceLevel.CONFIDENTIAL,
    },
    {
        "name": "Employee (Finance)",
        "username": "employee_fin",
        "email": "employee.fin@cortex.local",
        "password": "employee123",
        "job_title": "Finance Analyst",
        "role": Role.EMPLOYEE,
        "department": "Finance",
        "clearance_level": ClearanceLevel.INTERNAL,
    },
]


async def seed_users():
    await init_db()
    async with async_session() as check_session:
        existing_admin = await check_session.execute(select(User).where(User.username == "admin"))
        admin_exists = existing_admin.scalar_one_or_none() is not None

    if not admin_exists:
        admin_password = settings.CORTEX_BOOTSTRAP_ADMIN_PASSWORD.strip()
        if not admin_password:
            if settings.ENVIRONMENT.lower() in {"production", "prod"}:
                raise RuntimeError("Set CORTEX_BOOTSTRAP_ADMIN_PASSWORD before seeding a production database.")
            admin_password = secrets.token_urlsafe(24)
            print("\nOne-time development admin password (copy it now):")
            print(f"  username: admin\n  password: {admin_password}\n")
        INITIAL_USERS[0]["password"] = admin_password

    users_to_seed = INITIAL_USERS if settings.ENVIRONMENT.lower() not in {"production", "prod"} else INITIAL_USERS[:1]
    async with async_session() as session:
        for user_data in users_to_seed:
            stmt = select(User).where(
                (User.username == user_data["username"]) | (User.email == user_data["email"])
            )
            res = await session.execute(stmt)
            existing = res.scalars().first()
            if not existing:
                new_user = User(
                    name=user_data["name"],
                    username=user_data["username"],
                    email=user_data["email"],
                    job_title=user_data["job_title"],
                    hashed_password=hash_password(user_data["password"]),
                    role=user_data["role"],
                    department=user_data["department"],
                    clearance_level=user_data["clearance_level"],
                    token_version=1,
                    is_active=True,
                )
                session.add(new_user)
                print(f"Created user: {user_data['username']} ({user_data['name']}) -> {user_data['role'].value} / {user_data['department']}")
            else:
                print(f"User already exists: {user_data['username']}")
        await session.commit()
    print("Database seeding completed successfully.")


if __name__ == "__main__":
    asyncio.run(seed_users())
