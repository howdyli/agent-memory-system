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

    # ===== Embedding =====
    EMBEDDING_PROVIDER: str = "default"        # default(Chroma 内置) | local(sentence-transformers)
    EMBEDDING_MODEL: str = "BAAI/bge-small-zh-v1.5"  # 仅 provider=local 生效
    EMBEDDING_DEVICE: str = "cpu"              # cpu | cuda | mps
    EMBEDDING_BATCH_SIZE: int = 32             # 文档嵌入批处理大小
    EMBEDDING_WARMUP_ENABLED: bool = True      # 启动时后台预热 embedding 模型（G1 冷启动优化）
    EMBEDDING_WARMUP_TIMEOUT: float = 30.0     # 单次预热尝试超时（秒）
    EMBEDDING_WARMUP_RETRIES: int = 2          # 预热失败重试次数（指数退避）
    READINESS_WAIT_FOR_WARMUP: bool = True     # /health/ready 是否等待预热完成（failed 不阻断）
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

    # ===== Variables 备份（G3：Redis 主存 + 数据库 best-effort 镜像）=====
    # False 时双写与恢复全部短路；备份表过期行需周期调用 backup-purge 端点或依赖 restore 顺带清理
    VARIABLES_DB_BACKUP_ENABLED: bool = True

    # ===== 生命周期 =====
    COLD_MEMORY_THRESHOLD_DAYS: int = 30       # 冷记忆标记阈值（天）
    DEFAULT_HALF_LIFE_DAYS: int = 30           # 默认半衰期（天）

    # ===== 睡眠期记忆巩固（P1 Sleep-time Compute）=====
    CONSOLIDATION_ENABLED: bool = True             # 总开关；LLM 不可用/mock 时自动降级跳过
    CONSOLIDATION_SIMILARITY_THRESHOLD: float = 0.80  # 聚簇相似度阈值
    CONSOLIDATION_MIN_CLUSTER_SIZE: int = 2        # 最小簇大小（低于此值不巩固）
    CONSOLIDATION_MAX_CLUSTERS_PER_RUN: int = 10   # 单轮最多处理簇数（LLM 成本上限）
    CONSOLIDATION_MIN_AGE_HOURS: int = 24          # 只巩固创建超过 N 小时的记忆
    CONSOLIDATION_MAX_CANDIDATES_PER_RUN: int = 200  # 单轮每用户最多扫描候选数（嵌入计算成本上限，最旧优先）

    # ===== 多模态记忆（P2 R-13）=====
    MULTIMODAL_ENABLED: bool = True               # 多模态记忆总开关
    MULTIMODAL_UPLOAD_DIR: str = "data/uploads"   # 图片存储目录（相对 backend 根）
    MULTIMODAL_MAX_FILE_SIZE_MB: int = 10         # 单文件大小上限（MB）
    MULTIMODAL_ALLOWED_EXTENSIONS: str = "jpg,jpeg,png,gif,webp"  # 允许的图片扩展名
    VISION_MODEL: str = ""                        # Vision 模型（空 = 复用 DEEPSEEK_MODEL）

    # ===== 程序记忆（P2 R-14）=====
    PROCEDURE_EXTRACTION_ENABLED: bool = True      # 轨迹提炼总开关；LLM 不可用/mock 时自动降级跳过
    PROCEDURE_MIN_TRACE_CALLS: int = 2             # 至少 N 次工具调用的会话才提炼
    PROCEDURE_MAX_SESSIONS_PER_RUN: int = 20       # 单轮每用户最多提炼会话数（LLM 成本上限）

    # ===== 图谱社区检测（P2 R-15）=====
    COMMUNITY_DETECTION_RESOLUTION: float = 1.0    # Louvain resolution（越大社区越小越多）
    COMMUNITY_MIN_SIZE: int = 2                    # 最小社区大小（单点社区不入库）

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
