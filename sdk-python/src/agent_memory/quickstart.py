"""Zero-config quickstart factory (W3-F3.1).

用法::

    from agent_memory import quickstart

    mem = quickstart()                    # 嵌入模式 + ~/.agent-memory/default.db
    mem = quickstart(preset="chatbot")    # 预设配置
    mem.remember("user_name", "鑫海")
    ctx = mem.recall_context("鑫海的信息")

环境变量：
    AGENT_MEMORY_URL      有值时自动切换 HTTP 模式（作为 base_url）
    AGENT_MEMORY_API_KEY  HTTP 模式的 API Key
"""

import logging
import os
from pathlib import Path
from typing import Any, Optional

from agent_memory.client import MemoryClient
from agent_memory.presets import DEFAULT_PRESET, get_preset

logger = logging.getLogger(__name__)

# 嵌入模式默认数据库位置
DEFAULT_DB_DIR = Path.home() / ".agent-memory"
DEFAULT_DB_PATH = DEFAULT_DB_DIR / "default.db"

ENV_URL = "AGENT_MEMORY_URL"
ENV_API_KEY = "AGENT_MEMORY_API_KEY"


def _prepare_embedded_db(db_path: Path, user_id: int = 1) -> None:
    """确保数据库目录存在，并预置 backend SQLite 单例路径 + 执行迁移。

    SQLiteClient 是进程级单例，首次实例化决定 db 路径，
    因此必须在任何 service 调用前预先构造；嵌入模式不经过
    服务端启动流程，需主动跑 migrations 补齐 schema，并预置
    默认用户满足外键约束。
    backend 不可用时静默跳过（首次请求时 EmbeddedTransport 会给出明确报错）。
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        from app.core.db_client import SQLiteClient

        client = SQLiteClient(str(db_path))
        try:
            from app.core.migrations import run_migrations

            run_migrations(client)
        except Exception as e:
            logger.warning(f"嵌入模式迁移执行失败（不阻断）: {e}")
        try:
            client.execute(
                "INSERT OR IGNORE INTO users (id, username) VALUES (?, ?)",
                (user_id, f"quickstart_user_{user_id}"),
            )
        except Exception as e:
            logger.warning(f"嵌入模式预置默认用户失败（不阻断）: {e}")
    except ImportError:
        logger.debug("backend 包不可用，跳过 SQLite 单例预置")


def quickstart(
    preset: str = DEFAULT_PRESET,
    *,
    db_path: Optional[str] = None,
    user_id: int = 1,
    workspace_id: Optional[str] = None,
    **overrides: Any,
) -> MemoryClient:
    """零配置创建可用的 MemoryClient。

    自动检测环境变量 AGENT_MEMORY_URL / AGENT_MEMORY_API_KEY，
    有则使用 HTTP 模式；否则使用嵌入模式 + 默认 SQLite
    （~/.agent-memory/default.db，首次使用自动创建目录）。

    Args:
        preset: 预设配置名（chatbot / knowledge / assistant）
        db_path: 嵌入模式数据库路径（默认 ~/.agent-memory/default.db）
        user_id: 嵌入模式用户 ID
        workspace_id: 工作空间 ID
        **overrides: 覆盖预设中的任意配置项（如 recall_top_k=10）

    Returns:
        已应用预设配置的 MemoryClient
    """
    config = get_preset(preset)
    config.update(overrides)

    base_url = os.environ.get(ENV_URL, "").strip()
    if base_url:
        # HTTP 模式：环境变量优先
        api_key = os.environ.get(ENV_API_KEY, "").strip() or None
        logger.info(f"quickstart: 检测到 {ENV_URL}，使用 HTTP 模式")
        client = MemoryClient(
            base_url=base_url,
            api_key=api_key,
            workspace_id=workspace_id,
            mode="http",
        )
    else:
        # 嵌入模式：默认 SQLite
        path = Path(db_path).expanduser() if db_path else DEFAULT_DB_PATH
        _prepare_embedded_db(path, user_id=user_id)
        logger.info(f"quickstart: 使用嵌入模式，db_path={path}")
        client = MemoryClient(
            mode="embedded",
            db_path=str(path),
            user_id=user_id,
            workspace_id=workspace_id,
        )

    return client.configure(**config)
