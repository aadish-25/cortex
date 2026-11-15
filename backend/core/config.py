from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent.parent
ENV_FILE = BASE_DIR / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILE if ENV_FILE.exists() else ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Environment-specific variables (Loaded strictly from .env)
    ENVIRONMENT: str = "development"
    DATABASE_URL: str
    REDIS_URL: str
    QDRANT_URL: str
    QDRANT_API_KEY: str = ""
    JWT_SECRET_KEY: str
    JWT_ALGORITHM: str
    GEMINI_API_KEY: str = ""
    JINA_API_KEY: str = ""

    # Internal pipeline constants
    QDRANT_COLLECTION: str = "cortex_documents"
    EMBEDDING_MODEL: str = "BAAI/bge-small-en-v1.5"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440
    RERANKER_CONFIDENCE_THRESHOLD: float = 0.35
    DENSE_TOP_K: int = 25
    SPARSE_TOP_K: int = 25
    RRF_TOP_K: int = 25
    FINAL_TOP_K: int = 5


settings = Settings()  # type: ignore[call-arg]
