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
        import sqlalchemy as sa
        if "postgresql" in settings.DATABASE_URL:
            await conn.execute(sa.text("ALTER TABLE conversations ADD COLUMN IF NOT EXISTS summary TEXT;"))
            await conn.execute(sa.text("ALTER TABLE documents ADD COLUMN IF NOT EXISTS storage_path TEXT;"))
            await conn.execute(sa.text("ALTER TABLE messages ADD COLUMN IF NOT EXISTS is_complete BOOLEAN NOT NULL DEFAULT TRUE;"))
            await conn.execute(sa.text("""
                DELETE FROM message_feedback a USING message_feedback b
                WHERE a.message_id = b.message_id AND a.user_id = b.user_id
                  AND (a.created_at < b.created_at OR (a.created_at = b.created_at AND a.id::text < b.id::text))
            """))
        elif "sqlite" in settings.DATABASE_URL:
            document_columns = await conn.run_sync(
                lambda sync_conn: [row[1] for row in sync_conn.exec_driver_sql("PRAGMA table_info(documents)").fetchall()]
            )
            if "storage_path" not in document_columns:
                await conn.execute(sa.text("ALTER TABLE documents ADD COLUMN storage_path TEXT"))
            message_columns = await conn.run_sync(
                lambda sync_conn: [row[1] for row in sync_conn.exec_driver_sql("PRAGMA table_info(messages)").fetchall()]
            )
            if "is_complete" not in message_columns:
                await conn.execute(sa.text("ALTER TABLE messages ADD COLUMN is_complete BOOLEAN NOT NULL DEFAULT 1"))
            await conn.execute(sa.text("""
                DELETE FROM message_feedback
                WHERE rowid NOT IN (
                    SELECT MAX(rowid) FROM message_feedback GROUP BY message_id, user_id
                )
            """))
        await conn.execute(sa.text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_message_feedback_user ON message_feedback (message_id, user_id)"
        ))
        # Scrub legacy audit rows that stored login identifiers or usernames in free-text details.
        await conn.execute(sa.text("""
            UPDATE audit_logs SET details = 'Invalid credentials supplied'
            WHERE details LIKE 'Failed login attempt for identifier:%'
        """))
        await conn.execute(sa.text("""
            UPDATE audit_logs SET details = 'User account created; identifiers redacted'
            WHERE details LIKE 'Admin created user:%'
        """))
        await conn.execute(sa.text("""
            UPDATE audit_logs SET details = 'User account updated; identifiers redacted'
            WHERE details LIKE 'Admin updated user %'
        """))
        await conn.execute(sa.text("""
            UPDATE audit_logs SET details = 'User sessions revoked; identifiers redacted'
            WHERE details LIKE 'Admin % revoked sessions for user %'
        """))


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session() as session:
        yield session
