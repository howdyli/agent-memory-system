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

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": True,
    }


@lru_cache
def get_settings() -> Settings:
    """获取全局配置单例（带缓存）"""
    return Settings()
