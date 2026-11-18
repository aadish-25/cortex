from contextlib import asynccontextmanager
import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.core.config import settings
from backend.core.logging_config import setup_logging
from backend.db.database import init_db

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize enterprise rotating file logging into cortex/logs/general/
    setup_logging(mode="general")
    # Initialize database tables on startup
    await init_db()
    # Ensure Qdrant user memories collection and payload indexes exist
    try:
        from backend.db.qdrant import get_qdrant_client, init_user_memories_collection
        qdrant_client = get_qdrant_client()
        init_user_memories_collection(qdrant_client)
    except Exception as exc:
        logger.warning("Qdrant user memories collection init skipped at startup: %s", exc)
    yield


docs_url = "/docs" if settings.ENVIRONMENT == "development" else None
redoc_url = "/redoc" if settings.ENVIRONMENT == "development" else None
openapi_url = "/openapi.json" if settings.ENVIRONMENT == "development" else None

app = FastAPI(
    title="Cortex API",
    description="Enterprise RAG Assistant Backend (AAD-H1)",
    version="0.1.0",
    docs_url=docs_url,
    redoc_url=redoc_url,
    openapi_url=openapi_url,
    lifespan=lifespan,
)

# CORS configuration for Vite React frontend
allowed_origins = [origin.strip() for origin in settings.FRONTEND_ORIGINS.split(",") if origin.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Health check endpoint for zero-downtime deployment (DEPLOYMENT.md)
@app.get("/health", tags=["health"])
async def health_check():
    return {"status": "ok"}


# Register Routers
from backend.routers import actions, admin, auth, chat, documents, memories, users
app.include_router(auth.router)
app.include_router(users.router)
app.include_router(documents.router)
app.include_router(actions.router)
app.include_router(chat.router)
app.include_router(memories.router)
app.include_router(admin.router)
