"""
断路器模式（P1 LLM 容错增强）

为易失败的外部调用（如 LLM 后端）提供熔断保护，避免在后端持续故障时
反复重试拖垮系统。三态状态机：

- CLOSED：正常放行；连续失败达到 failure_threshold 后跳转 OPEN。
- OPEN：直接拒绝调用（allow() 返回 False）；经过 recovery_timeout 秒后
  进入 HALF_OPEN 试探。
- HALF_OPEN：仅放行 half_open_max_calls 次试探调用；成功则恢复 CLOSED，
  失败则重新 OPEN。

CircuitBreakerRegistry 按 (user_id, backend_name) 维护独立断路器实例，
并提供 snapshot() 供可观测端点读取。所有状态变更线程安全（threading.Lock）。
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, Tuple

logger = logging.getLogger(__name__)

# 断路器状态常量
STATE_CLOSED = "closed"
STATE_OPEN = "open"
STATE_HALF_OPEN = "half_open"


class CircuitBreaker:
    """单个断路器实例（线程安全）。"""

    def __init__(
        self,
        name: str = "default",
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        half_open_max_calls: int = 1,
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_max_calls = half_open_max_calls

        self._lock = threading.Lock()
        self._state = STATE_CLOSED
        self._failure_count = 0
        self._opened_at = 0.0
        self._half_open_calls = 0

    @property
    def state(self) -> str:
        """返回当前状态（会先按恢复超时做惰性状态推进）。"""
        with self._lock:
            self._maybe_recover()
            return self._state

    def _maybe_recover(self) -> None:
        """OPEN 状态经过 recovery_timeout 后转入 HALF_OPEN。调用方需持锁。"""
        if self._state == STATE_OPEN and (time.monotonic() - self._opened_at) >= self.recovery_timeout:
            self._state = STATE_HALF_OPEN
            self._half_open_calls = 0
            logger.info(f"断路器 [{self.name}] OPEN -> HALF_OPEN（恢复超时到达，开始试探）")

    def allow(self) -> bool:
        """是否放行本次调用。"""
        with self._lock:
            self._maybe_recover()
            if self._state == STATE_CLOSED:
                return True
            if self._state == STATE_OPEN:
                return False
            # HALF_OPEN：仅放行有限次试探
            if self._half_open_calls < self.half_open_max_calls:
                self._half_open_calls += 1
                return True
            return False

    def record_success(self) -> None:
        """记录一次成功调用。"""
        with self._lock:
            if self._state == STATE_HALF_OPEN:
                logger.info(f"断路器 [{self.name}] HALF_OPEN -> CLOSED（试探成功，恢复正常）")
            self._state = STATE_CLOSED
            self._failure_count = 0
            self._half_open_calls = 0

    def record_failure(self) -> None:
        """记录一次失败调用。"""
        with self._lock:
            if self._state == STATE_HALF_OPEN:
                # 试探失败，立即重新熔断
                self._state = STATE_OPEN
                self._opened_at = time.monotonic()
                self._half_open_calls = 0
                logger.warning(f"断路器 [{self.name}] HALF_OPEN -> OPEN（试探失败，重新熔断）")
                return

            self._failure_count += 1
            if self._state == STATE_CLOSED and self._failure_count >= self.failure_threshold:
                self._state = STATE_OPEN
                self._opened_at = time.monotonic()
                logger.warning(
                    f"断路器 [{self.name}] CLOSED -> OPEN"
                    f"（连续失败 {self._failure_count} 次达阈值 {self.failure_threshold}）"
                )

    def reset(self) -> None:
        """手动重置为 CLOSED。"""
        with self._lock:
            self._state = STATE_CLOSED
            self._failure_count = 0
            self._opened_at = 0.0
            self._half_open_calls = 0

    def snapshot(self) -> Dict[str, Any]:
        """返回当前状态快照（用于可观测）。"""
        with self._lock:
            self._maybe_recover()
            return {
                "name": self.name,
                "state": self._state,
                "failure_count": self._failure_count,
                "failure_threshold": self.failure_threshold,
                "recovery_timeout": self.recovery_timeout,
            }


class CircuitBreakerRegistry:
    """按 (user_id, backend_name) 维护断路器实例的注册表（线程安全）。"""

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        half_open_max_calls: int = 1,
    ):
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._half_open_max_calls = half_open_max_calls
        self._lock = threading.Lock()
        self._breakers: Dict[Tuple[int, str], CircuitBreaker] = {}

    def get(self, user_id: int, backend_name: str) -> CircuitBreaker:
        """获取（或惰性创建）指定后端的断路器。"""
        key = (user_id, backend_name)
        with self._lock:
            breaker = self._breakers.get(key)
            if breaker is None:
                breaker = CircuitBreaker(
                    name=f"{user_id}:{backend_name}",
                    failure_threshold=self._failure_threshold,
                    recovery_timeout=self._recovery_timeout,
                    half_open_max_calls=self._half_open_max_calls,
                )
                self._breakers[key] = breaker
            return breaker

    def snapshot(self) -> Dict[str, Any]:
        """返回全部断路器快照。"""
        with self._lock:
            breakers = list(self._breakers.values())
        snaps = [b.snapshot() for b in breakers]
        open_count = sum(1 for s in snaps if s["state"] == STATE_OPEN)
        return {
            "total": len(snaps),
            "open": open_count,
            "breakers": snaps,
        }

    def reset_all(self) -> None:
        """重置全部断路器（主要用于测试）。"""
        with self._lock:
            breakers = list(self._breakers.values())
        for b in breakers:
            b.reset()


# 全局注册表实例
_registry = CircuitBreakerRegistry()


def get_circuit_breaker_registry() -> CircuitBreakerRegistry:
    """获取全局断路器注册表实例。"""
    return _registry
