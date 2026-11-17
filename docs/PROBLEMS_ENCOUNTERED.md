# Problems Encountered

Running log of non-obvious bugs, environment issues, and their fixes.

---

## 1. passlib[bcrypt] broken on Python 3.14

**Symptom:** `AttributeError: module 'bcrypt' has no attribute '__about__'` when calling `CryptContext(schemes=["bcrypt"]).hash(...)`

**Cause:** `passlib` 1.7.4 probes `bcrypt.__about__.__version__` which was removed in `bcrypt` 4.x. Python 3.14 ships with a newer bcrypt.

**Fix:** Removed `passlib` entirely. Using `import bcrypt` directly with `bcrypt.hashpw` and `bcrypt.checkpw`. Password is explicitly truncated to 72 bytes before hashing to conform to bcrypt spec.

---

## 2. pytest cannot find `backend` module

**Symptom:** `ModuleNotFoundError: No module named 'backend'` when running `pytest` from `d:\rankuno\cortex`.

**Cause:** `backend` is not an installed package. pytest adds the test file's directory to `sys.path` but not the project root.

**Fix:** Add at the top of every test file:
```python
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(__file__, "..", "..")))
```

---

## 3. sessionmaker causes Pylance type errors with AsyncSession

**Symptom:** `Argument of type "AsyncEngine" cannot be assigned to parameter "bind"` and `Object of type "Session" cannot be used with "async with"`.

**Cause:** `sqlalchemy.orm.sessionmaker` is typed for sync sessions only. Passing `class_=AsyncSession` works at runtime but Pylance cannot infer the return type.

**Fix:** Use `sqlalchemy.ext.asyncio.async_sessionmaker` (introduced in SQLAlchemy 2.0) which is properly typed for async.

---

## 4. `__tablename__` causes Pylance reportAssignmentType errors

**Symptom:** `Type "Literal['users']" is not assignable to declared type "declared_attr[Unknown]"` on every SQLModel table class.

**Cause:** Pylance resolves `__tablename__` from SQLAlchemy's metaclass as `declared_attr[Unknown]`. Assigning a plain string literal conflicts with this type.

**Fix:** Annotate `__tablename__` as `ClassVar[str]` on every model:
```python
from typing import ClassVar
__tablename__: ClassVar[str] = "users"
```

---

## 5. Pylance reportCallIssue on `Settings()`

**Symptom:** `Arguments missing for parameters "DATABASE_URL", "REDIS_URL", "QDRANT_URL", "JWT_SECRET_KEY"` on `settings = Settings()`.

**Cause:** After removing defaults from required fields, Pylance treats them as required constructor arguments. pydantic-settings populates them from `.env` at runtime via its own `__init__` machinery, which Pylance cannot see statically.

**Fix:** `settings = Settings()  # type: ignore[call-arg]`

---

## 6. SQLite `check_same_thread` error in async context

**Symptom:** `ProgrammingError: SQLite objects created in a thread can only be used in that same thread` during async DB operations.

**Cause:** SQLite's default thread safety check blocks cross-thread access. Async uses a thread pool internally.

**Fix:** Pass `connect_args={"check_same_thread": False}` to `create_async_engine` when `DATABASE_URL` contains `sqlite`.

---

## 7. SQLAlchemy asyncio requires `greenlet`

**Symptom:** `ImportError: The SQLAlchemy asyncio module requires that the Python 'greenlet' library is installed.`

**Cause:** SQLAlchemy's async engine uses greenlet under the hood to manage execution contexts, but greenlet was missing from initial project dependencies.

**Fix:** Added `greenlet>=3.0.0` to project dependencies via `uv add greenlet`.

---

## 8. Pydantic-settings `.env` lookup from outside project root

**Symptom:** `pydantic_core._pydantic_core.ValidationError` when running python scripts from outside `d:\rankuno\cortex` because default `env_file=".env"` looks in current working directory.

**Cause:** Relative path `".env"` in `SettingsConfigDict` depends on process CWD.

**Fix:** Compute `BASE_DIR = Path(__file__).resolve().parent.parent` in `backend/config.py` and set `env_file=ENV_FILE if ENV_FILE.exists() else ".env"`.

---

## 9. Pylance Cannot Access Attribute `desc` on SQLModel Field

**Symptom:** `Cannot access attribute "desc" for class "datetime". Attribute "desc" is unknown.` on `User.created_at.desc()`.

**Cause:** Pylance sees `User.created_at` statically as the Python `datetime` class (from its type annotation), which does not have a `.desc()` method, even though SQLAlchemy instruments it at runtime.

**Fix:** Wrap the field in SQLModel's official `col()` helper: `col(User.created_at).desc()`.

---

## 10. Windows ProactorEventLoop vs. asyncpg Connection Pooling in Pytest

**Symptom:** `RuntimeError: Event loop is closed` when running sequential async test functions with pytest-asyncio and PostgreSQL.

**Cause:** Python 3.14 on Windows defaults to `ProactorEventLoop`. Default SQLAlchemy `AsyncAdaptedQueuePool` maintains open asyncpg connection handles bound to the event loop that created them. When pytest-asyncio closes each test function's event loop, connections remaining in the pool are orphaned and crash when accessed or closed by subsequent loops.

**Fix:** In `backend/db/database.py`, configured `create_async_engine(..., poolclass=NullPool)` when connecting to PostgreSQL. `NullPool` opens connections on-demand and terminates them cleanly within the active loop, preventing cross-loop contamination.

---

## 11. Psycopg Async Incompatibility with ProactorEventLoop on Windows

**Symptom:** `sqlalchemy.exc.InterfaceError: (psycopg.InterfaceError) Psycopg cannot use the 'ProactorEventLoop' to run in async mode.`

**Cause:** Psycopg 3's async C extensions require `SelectorEventLoop` on Windows and cannot operate with Windows Proactor IOCP without custom loop factory plumbing.

**Fix:** Standardized on `asyncpg>=0.30.0` with `postgresql+asyncpg://` which natively integrates with `ProactorEventLoop` on Windows.

---

## 12. Silent In-Memory Vector Store Masking Offline Docker Infrastructure

**Symptom:** Local development runs silently fell back to an embedded in-memory Qdrant instance whenever Docker Desktop was offline, leading to unexpected index wipes and loss of uploaded documents across process restarts.

**Cause:** `get_qdrant_client()` caught all connection exceptions and automatically instantiated `QdrantClient(":memory:")`.

**Fix:** Removed the embedded fallback. `get_qdrant_client()` now raises an actionable `ConnectionError` with clear instructions to run `python run.py` or `docker compose up -d`. In addition, `run.py` validates Docker daemon readiness and container health before launching backend services.

---
