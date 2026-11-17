import os
import sys
import asyncio
from collections.abc import AsyncGenerator
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

# Ensure project root is in sys.path for pytest (Problem 2 in PROBLEMS_ENCOUNTERED.md)
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.core.logging_config import setup_logging
from backend.db.database import async_session, init_db

# Initialize structured test logging into cortex/logs/test/
setup_logging(mode="test")

# NOTE ON CONFTEST.PY FIXTURES:
# By definition, pytest automatically discovers and loads 'conftest.py' at startup.
# Fixtures defined here (like `db_session` and `setup_database`) are globally available
# to all test files in this directory and its subdirectories.
# You NEVER need to import them into test files; pytest matches function parameter names
# (e.g. `def test_xyz(db_session):`) and injects them automatically via dependency injection.


@pytest.fixture(scope="session", autouse=True)
def setup_database():
    """Ensure database tables and schema migrations are initialized ONCE per test session."""
    asyncio.run(init_db())


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Provide an isolated database session per test.
    Automatically injected into any test declaring a `db_session` parameter.
    """
    async with async_session() as session:
        yield session
