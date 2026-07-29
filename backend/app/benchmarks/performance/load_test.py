"""
并发负载基准评测套件（L3）

验证多租户并发场景下的稳定性与资源占用。

负载模型：
    - 模拟 N 个并发 workspace（10/50/100）
    - 每个 workspace 持续混合操作：70% 召回 + 20% 写入 + 10% 图谱查询
    - 持续指定时长（默认 30s，CI 友好；release 可设 600s）

监控指标：
    - 错误率（目标 < 1%）
    - P99 延迟（目标 < 2000ms）
    - 内存占用峰值
    - 各操作类型的吞吐量与延迟分布

环境变量：
    PERF_LOAD_WORKSPACES  并发 workspace 数列表（默认 "10,50"）
    PERF_LOAD_DURATION    每轮持续秒数（默认 30）

用法：
    python -m app.benchmarks.runner run --suite load_test
    python -m app.benchmarks.runner run --suite load_test --duration 600
"""
from __future__ import annotations

import logging
import os
import threading
import time
import tracemalloc
from typing import Any, Dict, List, Optional

from app.benchmarks.base import BenchmarkResult, BenchmarkSuite, register_suite
from app.benchmarks.performance._utils import (
    PERF_SCALE,
    PERF_USER_ID,
    cleanup_user_data,
    percentile,
    seed_fragments,
)

logger = logging.getLogger(__name__)

# 目标：错误率 < 1%，P99 < 2000ms
TARGETS = {
    "success_rate": 0.99,
    "p99_ms": 2000.0 * PERF_SCALE,
}

_DEFAULT_WORKSPACES = [10, 50]
_DEFAULT_DURATION = int(os.getenv("PERF_LOAD_DURATION", "30"))


@register_suite
class LoadTestBenchmarkSuite(BenchmarkSuite):
    """L3 并发负载基准评测套件。"""

    name = "load_test"
    level = "L3"
    requires_llm = False
    requires_external = False
    description = "多 workspace 并发混合负载（70%召回+20%写入+10%图谱）"

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        # 并发 workspace 数
        cfg_ws = self.config.get("workspaces")
        if cfg_ws:
            self.workspace_levels = [int(w) for w in str(cfg_ws).split(",")]
        else:
            env_ws = os.getenv("PERF_LOAD_WORKSPACES", "")
            self.workspace_levels = (
                [int(w) for w in env_ws.split(",")] if env_ws else list(_DEFAULT_WORKSPACES)
            )
        self.duration: int = self.config.get("duration", _DEFAULT_DURATION)
        # 基础用户 ID，每个 workspace 分配独立 user_id 避免锁争用
        self.base_user_id: int = self.config.get("user_id", PERF_USER_ID)
        # 预置数据量（每 workspace）
        self.seed_per_workspace: int = self.config.get("seed_per_workspace", 200)
        self.results: List[Dict[str, Any]] = []

    def setup(self) -> None:
        """清理残留数据。"""
        # 清理基础用户及可能的 workspace 用户范围
        uids = [self.base_user_id + i for i in range(max(self.workspace_levels) + 10)]
        cleanup_user_data(uids)

    def run(self) -> Dict[str, Any]:
        """对每个并发级别跑混合负载。"""
        self.results = []
        for n_workspaces in self.workspace_levels:
            self._run_load_level(n_workspaces)
        return {
            "workspace_levels": self.workspace_levels,
            "duration_seconds": self.duration,
            "seed_per_workspace": self.seed_per_workspace,
            "levels": self.results,
        }

    def _run_load_level(self, n_workspaces: int) -> None:
        """单个并发级别的负载测试。"""
        logger.info(f"[load_test] 并发 {n_workspaces} workspace，持续 {self.duration}s")

        # 为每个 workspace 预置数据
        user_ids = [self.base_user_id + i for i in range(n_workspaces)]
        for uid in user_ids:
            seed_fragments(uid, self.seed_per_workspace)
            from app.benchmarks.performance._utils import seed_graph
            seed_graph(uid, 20)

        # 启动内存追踪
        tracemalloc.start()
        baseline_mem = tracemalloc.get_traced_memory()[0]

        # 共享状态
        latencies: List[float] = []
        op_counts = {"recall": 0, "write": 0, "graph": 0}
        error_counts = {"recall": 0, "write": 0, "graph": 0}
        lock = threading.Lock()
        stop_event = threading.Event()

        def worker(uid: int) -> None:
            """单 workspace 的混合负载 worker。"""
            from app.services.memory_fragment_service import (
                create_fragment, search_fragments_by_semantic,
            )
            from app.services.graph_memory_service import get_neighbors
            import random

            rng = random.Random(uid)
            local_counter = 0
            while not stop_event.is_set():
                local_counter += 1
                roll = rng.random()
                start = time.perf_counter()
                op_type = "recall"
                try:
                    if roll < 0.70:
                        # 召回
                        op_type = "recall"
                        q = rng.choice(["编程", "旅行", "美食", "健康", "阅读"])
                        search_fragments_by_semantic(
                            user_id=uid, query=q, top_k=10,
                        )
                    elif roll < 0.90:
                        # 写入
                        op_type = "write"
                        create_fragment(
                            user_id=uid, fragment_type="info",
                            content=f"负载测试片段 {uid}-{local_counter}",
                            importance_score=0.5,
                        )
                    else:
                        # 图谱查询
                        op_type = "graph"
                        ent_idx = local_counter % 20
                        get_neighbors(
                            user_id=uid,
                            entity_name=f"perf_entity_{ent_idx}",
                            entity_type="person",
                            depth=1,
                        )
                    elapsed_ms = (time.perf_counter() - start) * 1000.0
                    with lock:
                        latencies.append(elapsed_ms)
                        op_counts[op_type] += 1
                except Exception:
                    elapsed_ms = (time.perf_counter() - start) * 1000.0
                    with lock:
                        latencies.append(elapsed_ms)
                        op_counts[op_type] += 1
                        error_counts[op_type] += 1

        # 启动 worker 线程（每 workspace 一个）
        threads = [threading.Thread(target=worker, args=(uid,), daemon=True)
                   for uid in user_ids]
        start_time = time.perf_counter()
        for t in threads:
            t.start()

        # 运行指定时长
        time.sleep(self.duration)
        stop_event.set()

        # 等待线程退出（最多 5s）
        for t in threads:
            t.join(timeout=5.0)
        elapsed = time.perf_counter() - start_time

        # 内存峰值
        current_mem, peak_mem = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        peak_growth_mb = (peak_mem - baseline_mem) / (1024 * 1024)

        # 统计
        total_ops = sum(op_counts.values())
        total_errors = sum(error_counts.values())
        success_rate = (total_ops - total_errors) / max(1, total_ops)
        p50 = percentile(latencies, 50) if latencies else 0.0
        p95 = percentile(latencies, 95) if latencies else 0.0
        p99 = percentile(latencies, 99) if latencies else 0.0
        throughput = total_ops / elapsed if elapsed > 0 else 0.0

        result = {
            "n_workspaces": n_workspaces,
            "duration_seconds": round(elapsed, 2),
            "total_ops": total_ops,
            "total_errors": total_errors,
            "success_rate": round(success_rate, 4),
            "throughput_ops": round(throughput, 2),
            "p50_ms": round(p50, 2),
            "p95_ms": round(p95, 2),
            "p99_ms": round(p99, 2),
            "peak_memory_growth_mb": round(peak_growth_mb, 2),
            "op_breakdown": {
                op: {
                    "count": op_counts[op],
                    "errors": error_counts[op],
                    "error_rate": round(error_counts[op] / max(1, op_counts[op]), 4),
                }
                for op in ["recall", "write", "graph"]
            },
        }
        self.results.append(result)
        logger.info(
            f"[load_test] c={n_workspaces}: {total_ops} ops, "
            f"success={success_rate:.2%}, p99={p99:.0f}ms, "
            f"peak_mem=+{peak_growth_mb:.1f}MB"
        )

    def evaluate(self, raw_results: Dict[str, Any]) -> BenchmarkResult:
        """取最大并发级别的错误率与 P99 判定通过。"""
        levels = raw_results.get("levels", [])
        if not levels:
            return BenchmarkResult(
                suite_name=self.name, level=self.level,
                metrics={"success_rate": 0.0, "p99_ms": 0.0},
                targets=TARGETS, passed=False,
                details=[{"error": "无负载测试结果"}], raw=raw_results,
            )

        # 取最大并发级别的结果作为判定依据
        max_level = max(levels, key=lambda x: x.get("n_workspaces", 0))
        success_rate = max_level.get("success_rate", 0.0)
        p99 = max_level.get("p99_ms", 0.0)

        result = BenchmarkResult(
            suite_name=self.name,
            level=self.level,
            metrics={"success_rate": success_rate, "p99_ms": p99},
            targets=TARGETS,
            details=levels,
            raw=raw_results,
        )
        # success_rate 越高越好，p99_ms 越低越好，手动判定
        result.passed = (
            success_rate >= TARGETS["success_rate"]
            and p99 <= TARGETS["p99_ms"]
        )
        return result

    def teardown(self) -> None:
        """清理测试数据。"""
        uids = [self.base_user_id + i for i in range(max(self.workspace_levels) + 10)]
        cleanup_user_data(uids)
