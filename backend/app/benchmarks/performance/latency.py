"""
延迟基准评测套件（L3）

测量核心操作的 P50/P95/P99 延迟，明确独立记忆系统的性能边界。

覆盖操作：
    1. 记忆召回（recall, top_k=10）      — 目标 P99 < 500ms
    2. 语义搜索（向量）                   — 目标 P99 < 300ms
    3. 混合搜索（4 信号融合）             — 目标 P99 < 800ms
    4. 片段创建（含 embedding）           — 目标 P99 < 200ms
    5. 图谱邻居查询（depth=2）            — 目标 P99 < 300ms
    6. 自动召回（auto_recall）            — 目标 P99 < 1000ms

数据规模梯度：
    - small  (1K 片段)：CI 友好
    - medium (10K 片段)：nightly
    - large  (100K 片段)：release / 手动

环境变量：
    PERF_ITERATIONS   采样次数（默认 100）
    PERF_SCALE        阈值放宽系数（默认 1.0，CI 可设 2.0）
    PERF_USER_ID      测试用户 ID（默认 9800）

用法：
    python -m app.benchmarks.runner run --suite latency
    python -m app.benchmarks.runner run --suite latency --scale 1k
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.benchmarks.base import BenchmarkResult, BenchmarkSuite, register_suite
from app.benchmarks.performance._utils import (
    DEFAULT_ITERATIONS,
    PERF_SCALE,
    PERF_USER_ID,
    PERF_WORKSPACE_ID,
    cleanup_user_data,
    measure,
    resolve_scale,
    seed_fragments,
    seed_graph,
    summarize_latency,
)

logger = logging.getLogger(__name__)

# P99 延迟目标（毫秒）。通过 PERF_SCALE 整体放宽以适配 CI 慢机器。
TARGETS = {
    "recall_p99": 500.0 * PERF_SCALE,
    "semantic_p99": 300.0 * PERF_SCALE,
    "hybrid_p99": 800.0 * PERF_SCALE,
    "fragment_create_p99": 200.0 * PERF_SCALE,
    "graph_neighbors_p99": 300.0 * PERF_SCALE,
    "auto_recall_p99": 1000.0 * PERF_SCALE,
}


@register_suite
class LatencyBenchmarkSuite(BenchmarkSuite):
    """L3 延迟基准评测套件。"""

    name = "latency"
    level = "L3"
    requires_llm = False        # 延迟测试用 mock/本地路径，排除 LLM 干扰
    requires_external = False
    description = "核心操作 P50/P95/P99 延迟（召回/语义/混合/创建/图谱/自动召回）"

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self.scale: str = self.config.get("scale", "small")
        self.iterations: int = self.config.get("iterations", DEFAULT_ITERATIONS)
        self.user_id: int = self.config.get("user_id", PERF_USER_ID)
        self.workspace_id: Optional[int] = self.config.get(
            "workspace_id", PERF_WORKSPACE_ID
        )
        self.target_count: int = resolve_scale(self.scale)
        self.seeded_count: int = 0
        self.results: List[Dict[str, Any]] = []

    def setup(self) -> None:
        """预置测试数据：N 条片段 + 50 个图谱实体。"""
        logger.info(f"[latency] 预置数据：{self.target_count} 片段 + 50 图谱实体")
        cleanup_user_data([self.user_id])
        self.seeded_count = seed_fragments(
            self.user_id, self.target_count, self.workspace_id
        )
        seed_graph(self.user_id, 50, self.workspace_id)
        logger.info(f"[latency] 预置完成：实际写入 {self.seeded_count} 片段")
        # 关键校验：若预置失败，后续测量全是"空查询"，毫无意义
        if self.seeded_count < self.target_count * 0.9:
            raise RuntimeError(
                f"[latency] 预置数据不足: {self.seeded_count}/{self.target_count} "
                f"({self.seeded_count / self.target_count:.0%})，请检查 user_id={self.user_id} "
                f"是否存在、ChromaDB 是否可用、磁盘是否写满"
            )

    def run(self) -> Dict[str, Any]:
        """对每类操作采样 iterations 次，汇总延迟分布。"""
        self.results = []
        self._bench_recall()
        self._bench_semantic_search()
        self._bench_hybrid_search()
        self._bench_fragment_create()
        self._bench_graph_neighbors()
        self._bench_auto_recall()
        return {
            "scale": self.scale,
            "target_count": self.target_count,
            "seeded_count": self.seeded_count,
            "iterations": self.iterations,
            "operations": self.results,
        }

    # ============================================================
    # 各操作基准
    # ============================================================

    def _bench_recall(self) -> None:
        """记忆召回（RecallEngine 统一召回接口，top_k=10）。"""
        from app.services.recall_engine import RecallEngine

        engine = RecallEngine()
        queries = ["编程", "旅行", "美食", "健康", "阅读"]
        idx = {"i": 0}

        def op():
            q = queries[idx["i"] % len(queries)]
            idx["i"] += 1
            engine.recall(user_id=self.user_id, query=q, top_k=10)

        self._record("recall", op)

    def _bench_semantic_search(self) -> None:
        """语义搜索（向量检索）。"""
        from app.services.memory_fragment_service import search_fragments_by_semantic

        queries = ["编程语言", "旅游景点", "美食推荐", "健康生活", "阅读习惯"]
        idx = {"i": 0}

        def op():
            q = queries[idx["i"] % len(queries)]
            idx["i"] += 1
            search_fragments_by_semantic(
                user_id=self.user_id, query=q, top_k=10,
                workspace_id=self.workspace_id,
            )

        self._record("semantic_search", op)

    def _bench_hybrid_search(self) -> None:
        """混合搜索（4 信号融合：语义 + BM25 + 实体 + 时间）。"""
        from app.services.hybrid_search_service import hybrid_search

        queries = ["性能测试", "用户记录", "关键词", "编号", "领域"]
        idx = {"i": 0}

        def op():
            q = queries[idx["i"] % len(queries)]
            idx["i"] += 1
            hybrid_search(
                user_id=self.user_id, query=q, top_k=10,
                workspace_id=self.workspace_id,
            )

        self._record("hybrid_search", op)

    def _bench_fragment_create(self) -> None:
        """片段创建（含向量嵌入）。"""
        from app.services.memory_fragment_service import create_fragment

        counter = {"n": 0}

        def op():
            counter["n"] += 1
            create_fragment(
                user_id=self.user_id,
                fragment_type="info",
                content=f"延迟测试片段 #{counter['n']}",
                importance_score=0.5,
                workspace_id=self.workspace_id,
            )

        self._record("fragment_create", op)

    def _bench_graph_neighbors(self) -> None:
        """图谱邻居查询（depth=2）。"""
        from app.services.graph_memory_service import get_neighbors

        idx = {"i": 0}

        def op():
            # 轮询预置的实体
            i = idx["i"] % 50
            idx["i"] += 1
            get_neighbors(
                user_id=self.user_id,
                entity_name=f"perf_entity_{i}",
                entity_type="person",
                depth=2,
                workspace_id=self.workspace_id,
            )

        self._record("graph_neighbors", op)

    def _bench_auto_recall(self) -> None:
        """自动召回（auto_recall 完整流程）。"""
        from app.services.auto_recall_service import auto_recall

        queries = ["我有哪些编程记录", "最近的健康记忆", "旅行相关的内容"]
        idx = {"i": 0}

        def op():
            q = queries[idx["i"] % len(queries)]
            idx["i"] += 1
            auto_recall(
                user_id=self.user_id, query=q,
                workspace_id=self.workspace_id, top_k=10,
            )

        self._record("auto_recall", op)

    def _record(self, op_name: str, fn: Any) -> None:
        """执行采样并记录结果。"""
        samples, returns = measure(fn, iterations=self.iterations)
        error_count = sum(1 for r in returns if isinstance(r, dict) and "_error" in r)
        summary = summarize_latency(samples)
        summary["operation"] = op_name
        summary["error_count"] = error_count
        summary["error_rate"] = round(error_count / max(1, len(samples)), 4)
        self.results.append(summary)
        logger.info(
            f"[latency] {op_name}: p50={summary['p50']:.1f}ms "
            f"p95={summary['p95']:.1f}ms p99={summary['p99']:.1f}ms "
            f"(errors={error_count})"
        )

    # ============================================================
    # 评估与清理
    # ============================================================

    def evaluate(self, raw_results: Dict[str, Any]) -> BenchmarkResult:
        """提取各操作 P99，对照目标判定通过。"""
        ops = raw_results.get("operations", [])
        metrics: Dict[str, float] = {}
        for op in ops:
            name = op.get("operation", "")
            metrics[f"{name}_p50"] = op.get("p50", 0.0)
            metrics[f"{name}_p95"] = op.get("p95", 0.0)
            metrics[f"{name}_p99"] = op.get("p99", 0.0)
            metrics[f"{name}_mean"] = op.get("mean", 0.0)
            metrics[f"{name}_error_rate"] = op.get("error_rate", 0.0)

        # 映射到目标键
        target_metrics = {
            "recall_p99": metrics.get("recall_p99", 0.0),
            "semantic_p99": metrics.get("semantic_search_p99", 0.0),
            "hybrid_p99": metrics.get("hybrid_search_p99", 0.0),
            "fragment_create_p99": metrics.get("fragment_create_p99", 0.0),
            "graph_neighbors_p99": metrics.get("graph_neighbors_p99", 0.0),
            "auto_recall_p99": metrics.get("auto_recall_p99", 0.0),
        }

        result = BenchmarkResult(
            suite_name=self.name,
            level=self.level,
            metrics=target_metrics,
            targets=TARGETS,
            details=ops,
            raw=raw_results,
        )
        # 延迟指标：越低越好，手动判定（check_targets 默认 >= 不适用）
        result.passed = all(
            target_metrics.get(name, float("inf")) <= target
            for name, target in TARGETS.items()
        )
        # 错误率 > 5% 视为未通过（即使延迟达标）
        if result.passed:
            for op in ops:
                if op.get("error_rate", 0.0) > 0.05:
                    result.passed = False
                    break
        return result

    def teardown(self) -> None:
        """清理测试数据。"""
        cleanup_user_data([self.user_id])
