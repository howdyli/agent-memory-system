"""
Embedding 启动预热（G1：消除首次语义召回冷启动超时）

embedding 模型是懒加载的：local 模式在首次 embed_query 时加载
sentence-transformers 模型；default 模式由 ChromaDB 在首次 collection
查询时加载内置 all-MiniLM-L6-v2。首次语义召回因此延迟 5-8s，
易超出调用方的超时预算。

本模块在应用 startup 阶段由后台任务调用 warmup_embedding_models()
提前触发模型加载；/health/ready 依据 get_warmup_status() 做流量门控
（见 app/api/health.py）。

关键不变量：预热失败绝不阻断服务启动——warmup_embedding_models()
任何情况下不抛异常（除任务取消），failed 状态仍视为 ready（懒加载兜底），
避免实例被永久摘流。
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any, Dict

logger = logging.getLogger(__name__)

# 预热文本（硬编码即可）：中英文各一条，触发模型加载与首次推理
_WARMUP_TEXTS = (
    "预热查询：用户偏好与项目记忆",
    "warmup query for embedding model",
)

# 重试退避基数（秒），第 n 次重试前等待 base * 2^(n-1)；测试可缩小
_RETRY_BACKOFF_BASE = 0.5

_state_lock = threading.Lock()
_warmup_state: Dict[str, Any] = {
    "status": "pending",  # pending | warming_up | completed | failed
    "error": None,
    "start_time": None,
    "end_time": None,
}


def _update_state(**fields: Any) -> None:
    """加锁更新模块级预热状态。"""
    with _state_lock:
        _warmup_state.update(fields)


def _do_warmup() -> str:
    """同步预热逻辑（在线程池中执行），返回预热模式标识。"""
    from app.core.embedding import get_embedding_provider

    provider = get_embedding_provider()
    if provider is not None:
        # local 模式：embed_query 触发 sentence-transformers 模型加载与首次推理
        for text in _WARMUP_TEXTS:
            provider.embed_query(text)
        return "local"

    # default 模式：轻量查询触发 ChromaDB 内置模型（all-MiniLM-L6-v2）加载
    from app.core.chromadb_client import get_chromadb_client

    chroma = get_chromadb_client()
    if chroma is None:
        logger.info("ChromaDB 客户端不可用，跳过 default 模式 embedding 预热")
        return "skipped"
    chroma.search_embeddings("warmup", n_results=1)
    return "default"


async def warmup_embedding_models() -> bool:
    """执行 embedding 预热（startup 后台任务入口）。

    - EMBEDDING_WARMUP_ENABLED=False：直接标记 completed 并返回 True；
    - 每次尝试用线程 + EMBEDDING_WARMUP_TIMEOUT 超时保护；
    - 失败重试 EMBEDDING_WARMUP_RETRIES 次（指数退避）；
    - 最终失败标记 failed 并返回 False，任何情况下不抛异常
      （asyncio.CancelledError 除外，保证 shutdown 时可取消）。

    注意：超时后 wait_for 会取消等待，但预热线程本身无法中断，
    会在后台自然结束（模型加载完成后被单例缓存，不产生副作用）。
    """
    from app.core.config import get_settings

    try:
        settings = get_settings()
    except Exception as e:  # pragma: no cover — 配置读取失败的极端场景
        logger.error(f"✗ embedding 预热读取配置失败: {e}")
        _update_state(status="failed", error=str(e), end_time=time.time())
        return False

    if not settings.EMBEDDING_WARMUP_ENABLED:
        logger.info("embedding 预热已禁用（EMBEDDING_WARMUP_ENABLED=False），跳过")
        _update_state(status="completed", error=None, end_time=time.time())
        return True

    _update_state(status="warming_up", error=None, start_time=time.time(), end_time=None)
    max_attempts = max(0, settings.EMBEDDING_WARMUP_RETRIES) + 1
    last_error = ""
    for attempt in range(1, max_attempts + 1):
        try:
            mode = await asyncio.wait_for(
                asyncio.to_thread(_do_warmup),
                timeout=settings.EMBEDDING_WARMUP_TIMEOUT,
            )
            _update_state(status="completed", error=None, end_time=time.time())
            logger.info(f"✓ embedding 预热完成（mode={mode}, attempt={attempt}/{max_attempts}）")
            return True
        except asyncio.CancelledError:
            # 应用关闭时任务被取消：向上传播，由 lifespan 吞掉
            raise
        except Exception as e:
            last_error = f"{type(e).__name__}: {e}".strip().rstrip(":").strip()
            logger.warning(
                f"⚠️ embedding 预热第 {attempt}/{max_attempts} 次尝试失败: {last_error}"
            )
            if attempt < max_attempts:
                await asyncio.sleep(_RETRY_BACKOFF_BASE * (2 ** (attempt - 1)))

    _update_state(status="failed", error=last_error, end_time=time.time())
    logger.error(f"✗ embedding 预热最终失败（共 {max_attempts} 次尝试）: {last_error}")
    return False


def get_warmup_status() -> Dict[str, Any]:
    """返回预热状态快照（status/error/start_time/end_time）。"""
    with _state_lock:
        return dict(_warmup_state)


def is_warmup_ready() -> bool:
    """预热是否就绪：禁用或 completed 视为 ready。

    failed 也视为 ready=True：预热失败允许降级运行（懒加载兜底），
    避免预热失败导致实例被 readiness 永久摘流。
    """
    try:
        from app.core.config import get_settings

        if not get_settings().EMBEDDING_WARMUP_ENABLED:
            return True
    except Exception:  # pragma: no cover — 配置不可读时不阻断
        return True
    with _state_lock:
        status = _warmup_state["status"]
    return status in ("completed", "failed")


def _reset_warmup_state_for_tests() -> None:
    """重置模块级预热状态（仅测试使用）。"""
    _update_state(status="pending", error=None, start_time=None, end_time=None)


__all__ = [
    "warmup_embedding_models",
    "get_warmup_status",
    "is_warmup_ready",
]
