"""
集中配置模块（pydantic-settings）

将散落在各模块的 os.environ.get 收拢为类型安全的 Settings 类。
配置来源优先级：环境变量 > .env 文件 > 默认值
"""
from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """应用全局配置"""

    # ===== 关系型数据库 =====
    DATABASE_URL: str = "sqlite:///agent_memory.db"
    DB_POOL_SIZE: int = 10
    DB_ECHO: bool = False

    # ===== 向量数据库 =====
    VECTOR_BACKEND: str = "chroma"            # chroma | milvus | qdrant
    CHROMA_PERSIST_DIR: str = "./chromadb_data"
    MILVUS_URI: str = "localhost:19530"
    QDRANT_URL: str = "http://localhost:6333"

    # ===== Redis =====
    REDIS_URL: str = ""
    EVENT_BUS_BACKEND: str = "memory"         # memory | redis

    # ===== 认证 =====
    JWT_SECRET_KEY: str = ""
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRATION_HOURS: int = 24
    PBKDF2_ITERATIONS: int = 200000

    # ===== 加密 =====
    ENCRYPTION_PASSWORD: str = ""
    ENCRYPTION_SALT: str = ""

    # ===== CORS =====
    CORS_ORIGINS: str = ""

    # ===== 监控 =====
    ENABLE_METRICS: bool = True
    ENABLE_TRACING: bool = False
    OTLP_ENDPOINT: str = ""                   # http://otel-collector:4317

    # ===== 日志 =====
    LOG_LEVEL: str = "INFO"
    LOG_JSON: bool = False                    # 生产环境设为 True

    # ===== DeepSeek / LLM =====
    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_BASE_URL: str = ""
    DEEPSEEK_MODEL: str = ""

    # ===== 服务 =====
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    # ===== 调度器 =====
    OUTBOX_SCHEDULER_INTERVAL: int = 30        # 向量 outbox 处理间隔（秒）
    LIFECYCLE_SCHEDULER_INTERVAL: int = 30     # 生命周期调度间隔（秒）
    OUTBOX_MAX_RETRIES: int = 5                # outbox 最大重试次数

    # ===== LLM =====
    LLM_TIMEOUT_SECONDS: int = 30              # LLM 调用超时
    LLM_RETRY_DELAYS: str = "2,8,30"           # LLM 重试延迟（秒，逗号分隔）

    # ===== 限流 =====
    RATE_LIMIT_REQUESTS: int = 100             # 请求限流阈值
    RATE_LIMIT_WINDOW_SECONDS: int = 60        # 限流窗口（秒）

    # ===== 缓存 =====
    HYBRID_SEARCH_CACHE_TTL: int = 300         # 混合搜索结果缓存 TTL（秒）
    STATS_CACHE_TTL: int = 60                  # 统计接口缓存 TTL（秒）

    # ===== 生命周期 =====
    COLD_MEMORY_THRESHOLD_DAYS: int = 30       # 冷记忆标记阈值（天）
    DEFAULT_HALF_LIFE_DAYS: int = 30           # 默认半衰期（天）

    # ===== MCP Server =====
    MCP_ENABLED: bool = True                   # 是否启用 MCP Server
    MCP_TRANSPORT: str = "stdio"               # stdio | sse | streamable_http
    MCP_HOST: str = "127.0.0.1"                # MCP HTTP/SSE 监听地址
    MCP_PORT: int = 8765                       # MCP HTTP/SSE 监听端口
    MCP_DEFAULT_USER_ID: int = 1               # MCP 默认用户 ID（无认证时）
    MCP_DEFAULT_WORKSPACE_ID: Optional[int] = None  # MCP 默认 workspace ID
    MCP_REQUIRE_AUTH: bool = False             # MCP 是否强制认证（生产环境建议 True）

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": True,
    }


@lru_cache
def get_settings() -> Settings:
    """获取全局配置单例（带缓存）"""
    return Settings()
