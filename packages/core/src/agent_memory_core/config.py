"""
CoreConfig — Centralized configuration using pydantic-settings.

Replaces all scattered os.environ.get() calls across backend/services/ and backend/core/.
All settings are environment-variable-driven with sensible defaults.

Usage:
    from agent_memory_core.config import CoreConfig

    # Auto-load from env vars / .env file
    config = CoreConfig()

    # Or override programmatically
    config = CoreConfig(database_url="sqlite:///./mem.db", vector_backend="chroma")
"""

from pathlib import Path
from typing import Literal, Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class CoreConfig(BaseSettings):
    """Core-level configuration — pure logic, no HTTP/auth concerns."""

    model_config = SettingsConfigDict(
        env_prefix="AGENT_MEMORY_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ── Database ──────────────────────────────────────────────────
    database_url: str = Field(
        default="sqlite:///./agent_memory.db",
        description="Relational DB URL: sqlite:///path.db / postgresql://... / mysql://...",
    )
    database_echo: bool = Field(
        default=False,
        description="Echo SQL statements for debugging.",
    )

    # ── Vector Store ──────────────────────────────────────────────
    vector_backend: Literal["chroma", "milvus", "none"] = Field(
        default="chroma",
        description="Vector store backend: chroma (local) | milvus (remote) | none.",
    )
    chroma_path: str = Field(
        default="./chromadb_data",
        description="ChromaDB persistent data directory.",
    )
    milvus_host: str = Field(default="localhost", description="Milvus server host.")
    milvus_port: int = Field(default=19530, description="Milvus server port.")

    # ── Cache ─────────────────────────────────────────────────────
    cache_backend: Literal["redis", "fakeredis", "none"] = Field(
        default="fakeredis",
        description="Cache backend: redis | fakeredis (in-memory fallback) | none.",
    )
    redis_url: Optional[str] = Field(
        default=None,
        description="Redis URL. If empty and cache_backend=redis, defaults to redis://localhost:6379/0.",
    )

    # ── LLM ──────────────────────────────────────────────────────
    llm_api_key: Optional[str] = Field(
        default=None,
        description="API key for LLM service (OpenAI / DeepSeek / etc.).",
    )
    llm_base_url: Optional[str] = Field(
        default=None,
        description="Base URL for LLM API. Defaults depend on llm_provider.",
    )
    llm_provider: Literal["openai", "deepseek", "custom"] = Field(
        default="deepseek",
        description="LLM provider for defaults.",
    )
    llm_model: str = Field(
        default="deepseek-chat",
        description="Model name for LLM calls.",
    )
    llm_embedding_model: str = Field(
        default="text-embedding-3-small",
        description="Embedding model name.",
    )

    # ── Memory Defaults ───────────────────────────────────────────
    default_workspace_id: int = Field(
        default=1,
        description="Default workspace for operations without explicit workspace_id.",
    )
    default_user_id: int = Field(
        default=1,
        description="Default user for audit fields.",
    )
    context_max_tokens: int = Field(
        default=4000,
        description="Max token budget for context compression.",
    )
    recall_top_k: int = Field(
        default=5,
        description="Default number of results for recall/search.",
    )
    search_threshold: float = Field(
        default=0.3,
        description="Default similarity threshold for hybrid search.",
    )
    variable_default_ttl: Optional[int] = Field(
        default=None,
        description="Default TTL in seconds for variables (None = no expiry).",
    )

    # ── Security ─────────────────────────────────────────────────
    enable_sql_safety: bool = Field(
        default=True,
        description="Enable SQL safety checks for dynamic table queries.",
    )

    # ── LLM Provider Defaults ─────────────────────────────────────
    def get_llm_base_url(self) -> str:
        """Resolve LLM base URL based on provider if not explicitly set."""
        if self.llm_base_url:
            return self.llm_base_url
        defaults = {
            "openai": "https://api.openai.com/v1",
            "deepseek": "https://api.deepseek.com/v1",
            "custom": "",
        }
        return defaults.get(self.llm_provider, "")

    def get_llm_api_key(self) -> str:
        """Resolve LLM API key — must be set for any LLM operations."""
        if self.llm_api_key:
            return self.llm_api_key
        raise ValueError("AGENT_MEMORY_LLM_API_KEY must be set for LLM operations")


class ServerConfig(CoreConfig):
    """Server-level configuration — extends CoreConfig with HTTP/auth concerns."""

    model_config = SettingsConfigDict(
        env_prefix="AGENT_MEMORY_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ── Server ────────────────────────────────────────────────────
    server_host: str = Field(default="0.0.0.0", description="Server bind host.")
    server_port: int = Field(default=8000, description="Server bind port.")
    cors_origins: str = Field(
        default="*",
        description="CORS allowed origins (comma-separated or '*').",
    )

    # ── Authentication ────────────────────────────────────────────
    jwt_secret_key: str = Field(
        default="",
        description="JWT signing secret key. MUST be set for production.",
    )
    jwt_algorithm: str = Field(default="HS256", description="JWT algorithm.")
    jwt_expiration_hours: int = Field(default=24, description="JWT token lifetime in hours.")
    pbkdf2_iterations: int = Field(
        default=200000,
        description="PBKDF2 iterations for password hashing.",
    )
    encryption_password: Optional[str] = Field(
        default=None,
        description="Password for field-level encryption.",
    )
    encryption_salt: Optional[str] = Field(
        default=None,
        description="Salt for field-level encryption.",
    )

    # ── Monitoring ────────────────────────────────────────────────
    enable_metrics: bool = Field(
        default=True,
        description="Enable Prometheus metrics endpoint.",
    )
    enable_tracing: bool = Field(
        default=False,
        description="Enable OpenTelemetry tracing.",
    )
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field(
        default="INFO",
        description="Logging level.",
    )

    # ── EventBus ──────────────────────────────────────────────────
    event_bus_backend: Literal["memory", "redis"] = Field(
        default="memory",
        description="EventBus backend: memory (single-process) | redis (multi-process).",
    )
    webhook_max_retries: int = Field(
        default=3,
        description="Max retries for webhook delivery.",
    )
    webhook_retry_backoff: float = Field(
        default=2.0,
        description="Exponential backoff base for webhook retries.",
    )
