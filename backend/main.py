from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.core.config import settings
from backend.core.logging_config import setup_logging
from backend.db.database import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize enterprise rotating file logging into cortex/logs/general/
    setup_logging(mode="general")
    # Initialize database tables on startup
    await init_db()
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
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000", "*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Health check endpoint for zero-downtime deployment (DEPLOYMENT.md)
@app.get("/health", tags=["health"])
async def health_check():
    return {"status": "ok"}


# Register Routers
from backend.routers import actions, auth, chat, documents, users
app.include_router(auth.router)
app.include_router(users.router)
app.include_router(documents.router)
app.include_router(actions.router)
app.include_router(chat.router)
