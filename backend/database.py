from collections.abc import AsyncGenerator
from pathlib import Path
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from backend.config import BASE_DIR, settings

# Ensure relative SQLite path resolves relative to project root
db_url = settings.DATABASE_URL
if db_url.startswith("sqlite+aiosqlite:///./"):
    sqlite_filename = db_url.replace("sqlite+aiosqlite:///./", "")
    resolved_path = (BASE_DIR / sqlite_filename).resolve().as_posix()
    db_url = f"sqlite+aiosqlite:///{resolved_path}"

# SQLite connection args for async compatibility (Item 6 in PROBLEMS_ENCOUNTERED.md)
connect_args = {}
if "sqlite" in db_url:
    connect_args["check_same_thread"] = False

engine = create_async_engine(
    db_url,
    echo=False,
    future=True,
    connect_args=connect_args,
)

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
