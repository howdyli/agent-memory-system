"""
任务 4.2：性能基准测试（p99 延迟基线）

基线目标（本地/CI 参考值，非硬 SLA）：
  - 记忆变量 CRUD    p99 < 100ms
  - 事件发布(publish) p99 < 10ms
  - 错误响应构造      p99 < 1ms

全部标记 @pytest.mark.performance，默认被 pytest.ini 的 `-m "not performance"`
排除，仅在 nightly / 手动运行：
    pytest tests/test_performance_baselines.py -m performance --no-cov

说明：为降低 CI 抖动带来的假失败，阈值预留了充足余量，且允许通过环境变量
PERF_SCALE（默认 1.0）整体放宽（如 CI 机器较慢可设为 2.0）。
"""
import asyncio
import os
import time
from typing import Callable, List

import pytest

pytestmark = pytest.mark.performance

# 采样次数
ITERATIONS = 500
# 全局放宽系数（慢机器可通过环境变量调大）
PERF_SCALE = float(os.getenv("PERF_SCALE", "1.0"))


def _percentile(samples: List[float], pct: float) -> float:
    """计算百分位（samples 单位：毫秒）"""
    if not samples:
        return 0.0
    ordered = sorted(samples)
    k = max(0, min(len(ordered) - 1, int(round((pct / 100.0) * len(ordered)) - 1)))
    return ordered[k]


def _measure(fn: Callable[[], None], iterations: int = ITERATIONS) -> List[float]:
    """执行 fn 若干次，返回每次耗时（毫秒）列表。含少量预热。"""
    for _ in range(min(20, iterations)):  # 预热
        fn()
    samples: List[float] = []
    for _ in range(iterations):
        start = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - start) * 1000.0)
    return samples


# ============================================================
# 1. 记忆变量 CRUD p99 < 100ms
# ============================================================

class TestMemoryVariableCRUDPerformance:
    def test_memory_variable_crud_p99(self):
        from app.core.db_client import get_db_client
        db = get_db_client()
        user_id = 999  # conftest 约定的测试用户，测试后清理

        counter = {"n": 0}

        def crud_cycle():
            counter["n"] += 1
            key = f"perf_key_{counter['n'] % 50}"
            db.create_memory_variable(user_id, key, {"v": counter["n"]})
            db.get_memory_variable(user_id, key)

        samples = _measure(crud_cycle)
        p99 = _percentile(samples, 99)
        threshold = 100.0 * PERF_SCALE

        # 清理
        db.execute("DELETE FROM memory_variables WHERE user_id = ?", (user_id,))

        print(f"\n[记忆变量 CRUD] p50={_percentile(samples,50):.3f}ms "
              f"p99={p99:.3f}ms (阈值 {threshold:.0f}ms)")
        assert p99 < threshold, f"记忆变量 CRUD p99={p99:.3f}ms 超过阈值 {threshold:.0f}ms"


# ============================================================
# 2. 事件发布 p99 < 10ms
# ============================================================

class TestEventPublishPerformance:
    def test_event_publish_p99(self):
        from app.core.event_bus import InMemoryEventBus
        from app.core.events import MemoryEvent, EventType

        bus = InMemoryEventBus(buffer_size=20000)

        def publish_once():
            ev = MemoryEvent(event_type=EventType.MEMORY_CREATED, user_id=1)
            asyncio.run(bus.publish(ev))

        samples = _measure(publish_once, iterations=300)
        p99 = _percentile(samples, 99)
        threshold = 10.0 * PERF_SCALE

        print(f"\n[事件发布] p50={_percentile(samples,50):.3f}ms "
              f"p99={p99:.3f}ms (阈值 {threshold:.0f}ms)")
        assert p99 < threshold, f"事件发布 p99={p99:.3f}ms 超过阈值 {threshold:.0f}ms"


# ============================================================
# 3. 错误响应构造 p99 < 1ms
# ============================================================

class TestErrorResponsePerformance:
    def test_error_response_build_p99(self):
        from app.core.errors import ErrorResponse, ErrorCode

        def build_once():
            ErrorResponse.build(
                ErrorCode.NOT_FOUND,
                "Resource not found",
                details={"id": 123},
            )

        samples = _measure(build_once, iterations=ITERATIONS)
        p99 = _percentile(samples, 99)
        threshold = 1.0 * PERF_SCALE

        print(f"\n[错误响应构造] p50={_percentile(samples,50):.4f}ms "
              f"p99={p99:.4f}ms (阈值 {threshold:.1f}ms)")
        assert p99 < threshold, f"错误响应构造 p99={p99:.4f}ms 超过阈值 {threshold:.1f}ms"
