"""
进程内 LLM 异步重试队列（P1 LLM 容错增强）

当后台 LLM 调用（提取/生命周期/图谱/观测等非交互任务）失败或后端熔断时，
将任务入队，由后台协程按指数退避异步重试，避免任务直接丢失。

设计要点：
- 存储用线程安全的 deque + threading.Lock：enqueue 可能来自 FastAPI 同步
  线程池，直接操作 asyncio.Queue 跨线程不安全，故用 deque 承载。
- 后台 worker 为 asyncio 协程（镜像 webhook_service 的启停模式），周期性扫描
  到期任务并在 executor 中执行阻塞的 llm_chat，成功即丢弃、耗尽则告警丢弃。
- 队列上限 MAX_QUEUE_SIZE，满时丢弃最旧任务并计数。
- 仅服务后台任务；交互式 chat 不入队。
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections import deque
from typing import Any, Deque, Dict, List, Optional

logger = logging.getLogger(__name__)

# 指数退避间隔（秒），索引对应第 N 次重试
RETRY_BACKOFF = [2, 8, 30]
# 队列容量上限
MAX_QUEUE_SIZE = 1000
# worker 扫描间隔（秒）
_POLL_INTERVAL = 1.0

# 线程安全的任务存储
_lock = threading.Lock()
_queue: Deque[Dict[str, Any]] = deque()

# worker 状态
_worker_task: Optional[asyncio.Task] = None
_worker_running = False

# 统计指标
_stats = {
    "enqueued": 0,
    "retried_success": 0,
    "retried_exhausted": 0,
    "dropped": 0,
}


def enqueue_retry(
    user_id: int,
    messages: List[Dict[str, str]],
    kwargs: Optional[Dict[str, Any]] = None,
    max_attempts: int = 3,
) -> bool:
    """将失败的后台 LLM 任务入队等待异步重试。

    Args:
        user_id: 用户 ID
        messages: 聊天消息列表
        kwargs: 传给 llm_chat 的额外参数（不含 enqueue_on_failure）
        max_attempts: 最大重试次数

    Returns:
        是否成功入队
    """
    task = {
        "user_id": user_id,
        "messages": messages,
        "kwargs": dict(kwargs or {}),
        "attempts": 0,
        "max_attempts": max_attempts,
        "next_at": time.monotonic(),  # 首次立即可执行
    }
    with _lock:
        if len(_queue) >= MAX_QUEUE_SIZE:
            # 队列满：丢弃最旧任务
            _queue.popleft()
            _stats["dropped"] += 1
            logger.warning("LLM 重试队列已满，丢弃最旧任务")
        _queue.append(task)
        _stats["enqueued"] += 1
    logger.info(f"LLM 重试任务入队（user_id={user_id}, 队列深度={queue_depth()}）")
    return True


def queue_depth() -> int:
    """当前队列深度。"""
    with _lock:
        return len(_queue)


def get_stats() -> Dict[str, Any]:
    """返回队列统计快照（用于可观测）。"""
    with _lock:
        return {**_stats, "depth": len(_queue)}


def _pop_due_task() -> Optional[Dict[str, Any]]:
    """取出一个到期任务（next_at <= now）；无到期任务返回 None。"""
    now = time.monotonic()
    with _lock:
        for i, task in enumerate(_queue):
            if task["next_at"] <= now:
                del _queue[i]
                return task
    return None


def _requeue(task: Dict[str, Any]) -> None:
    """重试失败后按退避重新入队。"""
    attempt_idx = min(task["attempts"] - 1, len(RETRY_BACKOFF) - 1)
    delay = RETRY_BACKOFF[attempt_idx]
    task["next_at"] = time.monotonic() + delay
    with _lock:
        _queue.append(task)
    logger.warning(
        f"LLM 重试任务失败，{delay}s 后再试"
        f"（attempt {task['attempts']}/{task['max_attempts']}, user_id={task['user_id']}）"
    )


async def _process_task(task: Dict[str, Any]) -> None:
    """执行单个重试任务（在 executor 中调用阻塞的 llm_chat）。"""
    # 延迟导入避免循环依赖
    from app.services.llm_backend_service import llm_chat

    task["attempts"] += 1
    loop = asyncio.get_running_loop()

    def _call():
        # 重试路径不再入队，避免递归
        return llm_chat(
            task["user_id"],
            task["messages"],
            enqueue_on_failure=False,
            **task["kwargs"],
        )

    try:
        result = await loop.run_in_executor(None, _call)
    except Exception as e:
        result = {"success": False, "error": str(e)}

    if result.get("success"):
        with _lock:
            _stats["retried_success"] += 1
        logger.info(f"LLM 重试成功（user_id={task['user_id']}, attempt {task['attempts']}）")
        return

    if task["attempts"] >= task["max_attempts"]:
        with _lock:
            _stats["retried_exhausted"] += 1
        logger.error(
            f"LLM 重试耗尽，丢弃任务（user_id={task['user_id']}, "
            f"attempts={task['attempts']}）"
        )
        return

    _requeue(task)


async def _worker() -> None:
    """后台 worker 主循环：周期性扫描到期任务并处理。"""
    while _worker_running:
        try:
            task = _pop_due_task()
            if task is not None:
                await _process_task(task)
            else:
                await asyncio.sleep(_POLL_INTERVAL)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"LLM 重试 worker 异常: {e}")
            await asyncio.sleep(_POLL_INTERVAL)


def start_retry_worker() -> None:
    """启动 LLM 异步重试 worker。"""
    global _worker_task, _worker_running
    if _worker_running:
        return
    _worker_running = True
    _worker_task = asyncio.create_task(_worker())
    logger.info("LLM 异步重试 worker 已启动")


def stop_retry_worker() -> None:
    """停止 LLM 异步重试 worker。"""
    global _worker_task, _worker_running
    _worker_running = False
    if _worker_task:
        _worker_task.cancel()
        _worker_task = None
    logger.info("LLM 异步重试 worker 已停止")


def _clear_for_test() -> None:
    """清空队列与统计（仅测试使用）。"""
    with _lock:
        _queue.clear()
        for k in _stats:
            _stats[k] = 0
