"""
断路器测试（P1 LLM 容错增强）

覆盖：
- CircuitBreaker 状态机：CLOSED -> OPEN -> HALF_OPEN -> CLOSED/OPEN
- CircuitBreakerRegistry get/snapshot
- llm_chat 在断路器 OPEN 时跳过主后端（不再调用 backend.chat）
"""
import pytest

import app.core.circuit_breaker as cb_mod
from app.core.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerRegistry,
    STATE_CLOSED,
    STATE_OPEN,
    STATE_HALF_OPEN,
    get_circuit_breaker_registry,
)
from app.services import llm_backend_service as lbs


# ============================================================
# CircuitBreaker 状态机
# ============================================================

def test_closed_to_open_on_threshold():
    cb = CircuitBreaker(failure_threshold=3, recovery_timeout=60)
    assert cb.state == STATE_CLOSED
    assert cb.allow() is True

    cb.record_failure()
    cb.record_failure()
    assert cb.state == STATE_CLOSED  # 未达阈值

    cb.record_failure()  # 第 3 次达阈值
    assert cb.state == STATE_OPEN
    assert cb.allow() is False


def test_open_to_half_open_after_timeout(monkeypatch):
    # 用可控时钟模拟 recovery_timeout 到达
    clock = {"t": 1000.0}
    monkeypatch.setattr(cb_mod.time, "monotonic", lambda: clock["t"])

    cb = CircuitBreaker(failure_threshold=1, recovery_timeout=30.0, half_open_max_calls=1)
    cb.record_failure()
    assert cb.state == STATE_OPEN
    assert cb.allow() is False

    # 推进时钟越过恢复超时
    clock["t"] += 31.0
    # 惰性恢复：允许一次试探
    assert cb.allow() is True
    assert cb.state == STATE_HALF_OPEN
    # 试探配额用尽后不再放行
    assert cb.allow() is False


def test_half_open_success_closes(monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(cb_mod.time, "monotonic", lambda: clock["t"])

    cb = CircuitBreaker(failure_threshold=1, recovery_timeout=30.0)
    cb.record_failure()
    clock["t"] += 31.0
    assert cb.allow() is True  # -> HALF_OPEN
    cb.record_success()
    assert cb.state == STATE_CLOSED
    assert cb.allow() is True


def test_half_open_failure_reopens(monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(cb_mod.time, "monotonic", lambda: clock["t"])

    cb = CircuitBreaker(failure_threshold=1, recovery_timeout=30.0)
    cb.record_failure()
    clock["t"] += 31.0
    assert cb.allow() is True  # -> HALF_OPEN
    cb.record_failure()        # 试探失败
    assert cb.state == STATE_OPEN


def test_reset():
    cb = CircuitBreaker(failure_threshold=1)
    cb.record_failure()
    assert cb.state == STATE_OPEN
    cb.reset()
    assert cb.state == STATE_CLOSED


# ============================================================
# Registry
# ============================================================

def test_registry_get_and_snapshot():
    reg = CircuitBreakerRegistry(failure_threshold=1, recovery_timeout=60)
    b1 = reg.get(1, "openai")
    b1_again = reg.get(1, "openai")
    assert b1 is b1_again  # 同键复用同实例

    b2 = reg.get(2, "openai")
    assert b2 is not b1

    b1.record_failure()  # 使其 OPEN
    snap = reg.snapshot()
    assert snap["total"] == 2
    assert snap["open"] == 1
    assert len(snap["breakers"]) == 2


# ============================================================
# llm_chat 集成
# ============================================================

class _FailingBackend:
    def __init__(self):
        self.calls = 0

    def chat(self, messages, **kwargs):
        self.calls += 1
        return {"success": False, "error": "boom"}


class _EmptyDB:
    """无 fallback 后端的 DB 桩。"""
    def execute(self, sql, params=None):
        return []


def test_llm_chat_opens_and_skips_primary(monkeypatch):
    user_id = 987654
    backend = _FailingBackend()

    monkeypatch.setattr(
        lbs, "get_llm_backend",
        lambda uid, name=None: {"success": True, "backend": backend, "backend_name": "primary"},
    )
    # fallback chain 无其他后端
    monkeypatch.setattr(lbs, "_ensure_llm_config_table", lambda: None)
    monkeypatch.setattr(lbs, "get_db_client", lambda: _EmptyDB())

    registry = get_circuit_breaker_registry()
    registry.get(user_id, "primary").reset()

    # 连续失败达默认阈值 5 次 -> OPEN
    for _ in range(5):
        resp = lbs.llm_chat(user_id, [{"role": "user", "content": "hi"}])
        assert resp["success"] is False
        assert resp["degraded"] is True

    assert backend.calls == 5
    breaker = registry.get(user_id, "primary")
    assert breaker.state == STATE_OPEN

    # 断路器 OPEN：主后端被跳过，chat 调用次数不再增加
    resp = lbs.llm_chat(user_id, [{"role": "user", "content": "hi"}])
    assert backend.calls == 5
    assert resp["success"] is False
    assert resp.get("circuit_open") is True
    assert resp["degraded"] is True


def test_llm_chat_enqueue_on_failure(monkeypatch):
    """后台任务失败且开启入队时，标记 enqueued_for_retry。"""
    user_id = 987655
    backend = _FailingBackend()

    monkeypatch.setattr(
        lbs, "get_llm_backend",
        lambda uid, name=None: {"success": True, "backend": backend, "backend_name": "primary"},
    )
    monkeypatch.setattr(lbs, "_ensure_llm_config_table", lambda: None)
    monkeypatch.setattr(lbs, "get_db_client", lambda: _EmptyDB())

    enqueued = {}

    def fake_enqueue(uid, messages, kwargs, max_attempts=3):
        enqueued["called"] = (uid, messages)
        return True

    import app.services.llm_retry_queue as lrq
    monkeypatch.setattr(lrq, "enqueue_retry", fake_enqueue)

    get_circuit_breaker_registry().get(user_id, "primary").reset()

    resp = lbs.llm_chat(
        user_id, [{"role": "user", "content": "bg"}], enqueue_on_failure=True
    )
    assert resp["success"] is False
    assert resp.get("enqueued_for_retry") is True
    assert enqueued["called"][0] == user_id
