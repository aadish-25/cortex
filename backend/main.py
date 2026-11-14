from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.db.database import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize database tables on startup
    await init_db()
    yield


app = FastAPI(
    title="Cortex API",
    description="Enterprise RAG Assistant Backend (AAD-H1)",
    version="0.1.0",
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
from backend.routers import auth, users
app.include_router(auth.router)
app.include_router(users.router)
