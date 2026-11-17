from collections.abc import AsyncGenerator
from pathlib import Path
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel

from backend.core.config import BASE_DIR, settings

# Ensure relative SQLite path resolves relative to project root
db_url = settings.DATABASE_URL
if db_url.startswith("sqlite+aiosqlite:///./"):
    sqlite_filename = db_url.replace("sqlite+aiosqlite:///./", "")
    resolved_path = (BASE_DIR / sqlite_filename).resolve().as_posix()
    db_url = f"sqlite+aiosqlite:///{resolved_path}"

connect_args = {}
engine_kwargs = {
    "echo": False,
    "future": True,
}

if "sqlite" in db_url:
    connect_args["check_same_thread"] = False
    engine_kwargs["connect_args"] = connect_args
    engine_kwargs["pool_pre_ping"] = True
elif "postgresql" in db_url:
    # NullPool prevents asyncpg ProactorEventLoop binding conflicts across asyncio tasks/pytest loops
    engine_kwargs["poolclass"] = NullPool

engine = create_async_engine(db_url, **engine_kwargs)

# async_sessionmaker properly typed for AsyncSession (Item 3 in PROBLEMS_ENCOUNTERED.md)
async_session = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


async def init_db() -> None:
    async with engine.begin() as conn:
        # Import models so SQLModel metadata registers all tables
        import backend.models  # noqa: F401
        await conn.run_sync(SQLModel.metadata.create_all)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session() as session:
        yield session
