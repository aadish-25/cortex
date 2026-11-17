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
    # Provider Multi-Key Rotation Pools (Priority: Groq -> OpenRouter -> Gemini)
    GROQ_API_KEYS: str = ""
    OPENROUTER_API_KEYS: str = ""
    GEMINI_API_KEYS: str = ""

    GROQ_API_KEY: str = ""
    OPENROUTER_API_KEY: str = ""
    GEMINI_API_KEY: str = ""
    ANTHROPIC_API_KEY: str = ""
    OPENAI_API_KEY: str = ""
    META_API_KEY: str = ""
    JINA_API_KEY: str = ""

    # Jira Service Management (JSM) & Atlassian Cloud Integration
    JIRA_BASE_URL: str = ""
    JIRA_API_EMAIL: str = ""
    JIRA_API_TOKEN: str = ""
    JIRA_DEFAULT_PROJECT_KEY: str = "ITSD"

    # Provider Default Models
    GROQ_DEFAULT_MODEL: str = "groq/qwen/qwen3.8-27b"
    OPENROUTER_DEFAULT_MODEL: str = "openrouter/nvidia/nemotron-3.5-lightning:free"
    GEMINI_DEFAULT_MODEL: str = "gemini/gemini-2.0-flash"

    # LLM Gateway & Active Provider Configuration
    DEFAULT_LLM_PROVIDER: str = "groq"
    DEFAULT_LLM_MODEL: str = "groq/qwen/qwen3.8-27b"
    FAST_ROUTING_MODEL: str = "groq/qwen/qwen3.8-27b"
    SYNTHESIS_MODEL: str = "groq/qwen/qwen3.8-27b"

    # Supported Model Registry (LiteLLM Compatible)
    AVAILABLE_MODELS: list[str] = [
        "groq/qwen/qwen3.8-27b",
        "groq/openai/gpt-oss-120b",
        "openrouter/nvidia/nemotron-3.5-lightning:free",
        "openrouter/deepseek/deepseek-chat",
        "openrouter/anthropic/claude-3.5-sonnet",
        "gemini/gemini-2.5-flash",
        "gemini/gemini-1.5-pro",
        "claude-3-7-sonnet-20250219",
        "claude-3-5-haiku-20241022",
        "gpt-4o",
        "gpt-4o-mini",
    ]

    # Internal pipeline constants
    QDRANT_COLLECTION: str = "cortex_documents"
    QDRANT_USER_MEMORIES_COLLECTION: str = "cortex_user_memories"
    MAX_CONVERSATION_TOKENS: int = 8000
    EMBEDDING_MODEL: str = "BAAI/bge-small-en-v1.5"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440
    RERANKER_CONFIDENCE_THRESHOLD: float = 0.35
    DENSE_TOP_K: int = 25
    SPARSE_TOP_K: int = 25
    RRF_TOP_K: int = 25
    FINAL_TOP_K: int = 5


settings = Settings()  # type: ignore[call-arg]
