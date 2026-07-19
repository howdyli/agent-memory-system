"""
进程内 LLM 异步重试队列测试（P1 LLM 容错增强）

覆盖：入队 / 队列上限丢弃 / 后台重试成功 / 重试耗尽 / worker 启停。
"""
import asyncio

import pytest

from app.services import llm_retry_queue as lrq


@pytest.fixture(autouse=True)
def _clean_queue():
    lrq._clear_for_test()
    yield
    # 确保 worker 停止并清理
    lrq.stop_retry_worker()
    lrq._clear_for_test()


async def _wait_for(cond, timeout=3.0, interval=0.02):
    """轮询等待条件成立。"""
    elapsed = 0.0
    while elapsed < timeout:
        if cond():
            return True
        await asyncio.sleep(interval)
        elapsed += interval
    return cond()


def test_enqueue_increments_depth():
    assert lrq.queue_depth() == 0
    lrq.enqueue_retry(1, [{"role": "user", "content": "hi"}])
    assert lrq.queue_depth() == 1
    stats = lrq.get_stats()
    assert stats["enqueued"] == 1
    assert stats["depth"] == 1


def test_queue_full_drops_oldest(monkeypatch):
    monkeypatch.setattr(lrq, "MAX_QUEUE_SIZE", 2)
    lrq.enqueue_retry(1, [{"role": "user", "content": "a"}])
    lrq.enqueue_retry(1, [{"role": "user", "content": "b"}])
    lrq.enqueue_retry(1, [{"role": "user", "content": "c"}])  # 触发丢弃最旧
    stats = lrq.get_stats()
    assert stats["depth"] == 2
    assert stats["dropped"] == 1


async def test_worker_retries_success(monkeypatch):
    def fake_llm_chat(user_id, messages, enqueue_on_failure=False, **kwargs):
        return {"success": True, "response": "ok"}

    import app.services.llm_backend_service as lbs
    monkeypatch.setattr(lbs, "llm_chat", fake_llm_chat)

    lrq.start_retry_worker()
    lrq.enqueue_retry(1, [{"role": "user", "content": "retry me"}])

    ok = await _wait_for(lambda: lrq.get_stats()["retried_success"] >= 1)
    assert ok
    assert lrq.queue_depth() == 0
    assert lrq.get_stats()["retried_success"] == 1


async def test_worker_retries_exhausted(monkeypatch):
    monkeypatch.setattr(lrq, "RETRY_BACKOFF", [0, 0, 0])

    def fake_llm_chat(user_id, messages, enqueue_on_failure=False, **kwargs):
        return {"success": False, "error": "still failing"}

    import app.services.llm_backend_service as lbs
    monkeypatch.setattr(lbs, "llm_chat", fake_llm_chat)

    lrq.start_retry_worker()
    lrq.enqueue_retry(1, [{"role": "user", "content": "doomed"}], max_attempts=2)

    ok = await _wait_for(lambda: lrq.get_stats()["retried_exhausted"] >= 1)
    assert ok
    assert lrq.queue_depth() == 0
    assert lrq.get_stats()["retried_exhausted"] == 1


async def test_worker_start_stop():
    lrq.start_retry_worker()
    assert lrq._worker_running is True
    lrq.start_retry_worker()  # 幂等
    assert lrq._worker_running is True
    lrq.stop_retry_worker()
    assert lrq._worker_running is False
