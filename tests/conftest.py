import asyncio
from collections.abc import AsyncGenerator
import os
import sys

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

# Ensure project root is in sys.path for pytest (Problem 2 in PROBLEMS_ENCOUNTERED.md)
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.core.logging_config import setup_logging
from backend.db.database import async_session, init_db

# Initialize structured test logging into cortex/logs/test/
setup_logging(mode="test")


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-llm",
        action="store_true",
        default=False,
        help="Run tests marked @pytest.mark.llm that invoke live external LLM providers.",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    run_llm = config.getoption("--run-llm") or os.getenv("CORTEX_RUN_LLM_TESTS", "").lower() in {"1", "true", "yes"}
    if run_llm:
        return
    skip_llm = pytest.mark.skip(reason="Skipped live-LLM test by default (pass --run-llm or set CORTEX_RUN_LLM_TESTS=1)")
    for item in items:
        if "llm" in item.keywords:
            item.add_marker(skip_llm)


@pytest.fixture(scope="session", autouse=True)
def setup_database():
    """Ensure database tables and schema migrations are initialized ONCE per test session."""
    asyncio.run(init_db())


@pytest.fixture
def async_session_factory():
    """Provides the shared SQLAlchemy async_sessionmaker factory for unit tests."""
    return async_session


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Provide an isolated database session per test."""
    async with async_session() as session:
        yield session
