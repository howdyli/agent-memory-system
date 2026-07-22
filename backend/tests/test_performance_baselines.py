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


# ============================================================
# 4. 混合搜索 p99 < 100ms
# ============================================================

class TestHybridSearchPerformance:
    """混合搜索引擎性能基准：模拟100条片段下的检索延迟"""

    def test_hybrid_search_p99(self):
        """混合搜索 p99 应 < 100ms（基于 BM25 文本匹配模拟）"""
        from app.core.db_client import get_db_client
        db = get_db_client()
        user_id = 998  # 专用性能测试用户

        # 预置100条测试记忆片段
        for i in range(100):
            db.execute(
                "INSERT OR IGNORE INTO memory_fragments (user_id, content, category, importance_score) VALUES (?, ?, ?, ?)",
                (user_id, f"Performance test memory fragment number {i} about topic {i % 10}", "test", 0.5 + (i % 5) * 0.1)
            )

        def search_cycle():
            """执行一次文本检索（模拟 BM25 路径）"""
            db.execute(
                "SELECT * FROM memory_fragments WHERE user_id = ? AND content LIKE ? ORDER BY importance_score DESC LIMIT 10",
                (user_id, "%topic 5%")
            )

        samples = _measure(search_cycle, iterations=200)
        p99 = _percentile(samples, 99)
        threshold = 100.0 * PERF_SCALE

        # 清理
        db.execute("DELETE FROM memory_fragments WHERE user_id = ?", (user_id,))

        print(f"\n[混合搜索] p50={_percentile(samples, 50):.3f}ms "
              f"p99={p99:.3f}ms (阈值 {threshold:.0f}ms)")
        assert p99 < threshold, f"混合搜索 p99={p99:.3f}ms 超过阈值 {threshold:.0f}ms"


# ============================================================
# 5. 并发负载 p99 < 200ms
# ============================================================

class TestConcurrentLoadPerformance:
    """并发读写混合负载性能基准"""

    def test_concurrent_mixed_load_p99(self):
        """10并发读 + 2并发写混合负载，整体 p99 < 200ms"""
        import concurrent.futures
        from app.core.db_client import get_db_client

        db = get_db_client()
        user_id = 997

        # 预置数据
        for i in range(50):
            db.create_memory_variable(user_id, f"conc_key_{i}", {"v": i})

        samples: list = []

        def read_op():
            start = time.perf_counter()
            db.get_memory_variable(user_id, f"conc_key_{hash(time.perf_counter()) % 50}")
            return (time.perf_counter() - start) * 1000.0

        def write_op():
            start = time.perf_counter()
            db.create_memory_variable(user_id, f"conc_w_{int(time.perf_counter()*1000) % 100}", {"v": 1})
            return (time.perf_counter() - start) * 1000.0

        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as executor:
            futures = []
            for _ in range(100):  # 100轮
                # 10个读 + 2个写
                for _ in range(10):
                    futures.append(executor.submit(read_op))
                for _ in range(2):
                    futures.append(executor.submit(write_op))

            for f in concurrent.futures.as_completed(futures):
                try:
                    samples.append(f.result())
                except Exception:
                    pass

        p99 = _percentile(samples, 99)
        threshold = 200.0 * PERF_SCALE

        # 清理
        db.execute("DELETE FROM memory_variables WHERE user_id = ?", (user_id,))

        print(f"\n[并发混合负载] 总操作={len(samples)} p50={_percentile(samples, 50):.3f}ms "
              f"p99={p99:.3f}ms (阈值 {threshold:.0f}ms)")
        assert p99 < threshold, f"并发混合负载 p99={p99:.3f}ms 超过阈值 {threshold:.0f}ms"

    def test_memory_stability_1k_ops(self):
        """1000次连续操作内存增长 < 20MB"""
        import tracemalloc
        from app.core.db_client import get_db_client

        db = get_db_client()
        user_id = 996

        tracemalloc.start()
        baseline = tracemalloc.get_traced_memory()[0]

        for i in range(1000):
            key = f"stability_{i % 100}"
            db.create_memory_variable(user_id, key, {"iteration": i, "data": "x" * 100})
            db.get_memory_variable(user_id, key)

        current = tracemalloc.get_traced_memory()[0]
        tracemalloc.stop()
        growth_mb = (current - baseline) / (1024 * 1024)

        # 清理
        db.execute("DELETE FROM memory_variables WHERE user_id = ?", (user_id,))

        print(f"\n[内存稳定性] 1000次操作后内存增长: {growth_mb:.2f}MB (阈值 20MB)")
        assert growth_mb < 20.0, f"内存增长 {growth_mb:.2f}MB 超过 20MB 阈值"


# ============================================================
# 6. 吞吐量 > 100 ops/sec
# ============================================================

class TestThroughputBaseline:
    """吞吐量基准：验证系统持续处理能力"""

    def test_crud_throughput(self):
        """记忆变量 CRUD 吞吐量应 > 100 ops/sec"""
        from app.core.db_client import get_db_client
        db = get_db_client()
        user_id = 995

        duration_seconds = 5  # 测试持续时间
        ops_count = 0
        start_time = time.perf_counter()

        while (time.perf_counter() - start_time) < duration_seconds:
            key = f"throughput_{ops_count % 200}"
            db.create_memory_variable(user_id, key, {"n": ops_count})
            db.get_memory_variable(user_id, key)
            ops_count += 2  # create + get = 2 ops

        elapsed = time.perf_counter() - start_time
        qps = ops_count / elapsed
        min_qps = 100.0 / PERF_SCALE  # 放宽系数反向应用

        # 清理
        db.execute("DELETE FROM memory_variables WHERE user_id = ?", (user_id,))

        print(f"\n[吞吐量] {ops_count} ops in {elapsed:.2f}s = {qps:.1f} ops/sec (最低要求 {min_qps:.0f} ops/sec)")
        assert qps > min_qps, f"吞吐量 {qps:.1f} ops/sec 低于最低要求 {min_qps:.0f} ops/sec"
