"""
LoCoMo 基准评测套件（L2）

LoCoMo（Long Conversational Memory）是 Snap Research 发布的长期记忆基准，
包含 1540 道问题，覆盖 4 个维度：记忆提取/时间推理/弃权/多跳推理。

与 LongMemEval 互补：LongMemEval 侧重会话级，LoCoMo 侧重多跳推理。

特性：
    - 复用 run_benchmark 的多数票与 Wilson CI
    - 4 维度分类统计
    - 竞品对比表（Zep 94.7%、Letta 74.0%）

数据集获取：
    git clone https://github.com/snap-research/locomo.git
    数据文件放入 backend/app/benchmarks/data/locomo/
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from app.benchmarks.base import BenchmarkResult, BenchmarkSuite, register_suite

logger = logging.getLogger(__name__)

# 默认数据目录
_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "locomo")

# 目标：准确率 ≥ 60%（参考 Letta 74.0% 为上限目标）
TARGETS = {
    "accuracy": 0.60,
}


@register_suite
class LoComoSuite(BenchmarkSuite):
    """L2 LoCoMo 基准评测套件。

    用法：
        # 限量测试
        python -m app.benchmarks.runner run --suite locomo \\
            --data app/benchmarks/data/locomo --limit 50 --repeat 3

        # 全量 1540 题
        python -m app.benchmarks.runner run --suite locomo \\
            --data app/benchmarks/data/locomo --repeat 3
    """

    name = "locomo"
    level = "L2"
    requires_llm = True
    requires_external = False
    description = "LoCoMo 基准准确率（4 维度：提取/时间/弃权/多跳 + 竞品对比）"

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self.data_path: Optional[str] = self.config.get("data_path")
        self.limit: int = self.config.get("limit", 0)
        self.repeat: int = self.config.get("repeat", 1)
        self.user_id: int = self.config.get("user_id", 999)
        self.workspace_id: Optional[int] = self.config.get("workspace_id")
        self.use_llm_judge: bool = self.config.get("use_llm_judge", True)
        self.use_llm_answer: bool = self.config.get("use_llm_answer", True)
        self.top_k: int = self.config.get("top_k", 10)
        self.instances: List[Dict[str, Any]] = []

    def setup(self) -> None:
        """加载 LoCoMo 数据集。"""
        from app.benchmarks.locomo_adapter import load_locomo_dataset

        data_path = self.data_path or _DATA_DIR
        self.instances = load_locomo_dataset(data_path)

        # 限量
        if self.limit > 0 and self.limit < len(self.instances):
            # 分层抽样：按能力类别等比例抽样
            self.instances = _stratified_sample(self.instances, self.limit)
            logger.info(f"分层抽样限制为 {self.limit} 条实例")

    def run(self) -> Dict[str, Any]:
        """运行 LoCoMo 基准测试。"""
        from app.benchmarks.runner import run_benchmark

        results = run_benchmark(
            instances=self.instances,
            user_id=self.user_id,
            workspace_id=self.workspace_id,
            use_llm_judge=self.use_llm_judge,
            use_llm_answer=self.use_llm_answer,
            top_k_recall=self.top_k,
            repeat=self.repeat,
        )

        # 覆盖 ability 字段为 LoCoMo 的能力分类
        # run_benchmark 使用 longmemeval_adapter.get_ability，需修正为 LoCoMo 分类
        from app.benchmarks.locomo_adapter import get_ability as locomo_get_ability
        for result in results.get("results", []):
            qid = result.get("question_id", "")
            # 找到原始实例
            inst = next((i for i in self.instances if i.get("question_id") == qid), None)
            if inst:
                result["ability"] = locomo_get_ability(inst)

        # 重新计算指标（因为 ability 被修正）
        from app.benchmarks.evaluator import compute_metrics
        results["metrics"] = compute_metrics(results.get("results", []))

        return results

    def evaluate(self, raw_results: Dict[str, Any]) -> BenchmarkResult:
        """提取准确率与 Wilson CI，构建竞品对比。"""
        metrics = raw_results.get("metrics", {})
        accuracy = metrics.get("accuracy", 0.0)
        ci_low = metrics.get("accuracy_ci_low", 0.0)
        ci_high = metrics.get("accuracy_ci_high", 0.0)

        # 4 维度分类详情
        by_ability = metrics.get("by_ability", {})

        # 竞品对比
        competitor_comparison = _build_locomo_competitor_comparison(accuracy, ci_low, ci_high)

        # 稳定性
        stability = metrics.get("stability", {})

        result = BenchmarkResult(
            suite_name=self.name,
            level=self.level,
            metrics={
                "accuracy": accuracy,
                "accuracy_ci_low": ci_low,
                "accuracy_ci_high": ci_high,
            },
            targets=TARGETS,
            details=[{
                "total": metrics.get("total", 0),
                "correct": metrics.get("correct", 0),
                "ci": f"[{ci_low:.3f}, {ci_high:.3f}]",
                "by_ability": by_ability,
                "stability": stability,
                "competitor_comparison": competitor_comparison,
                "repeat": self.repeat,
                "metadata": raw_results.get("metadata", {}),
            }],
            raw=raw_results,
        )
        result.check_targets()
        return result

    def teardown(self) -> None:
        """清理评测数据。"""
        try:
            from app.core.db_client import get_db_client
            db = get_db_client()
            db.execute("DELETE FROM memory_fragments WHERE user_id = ?", (self.user_id,))
            db.execute("DELETE FROM memory_variables WHERE user_id = ?", (self.user_id,))
        except Exception as e:
            logger.debug(f"清理失败: {e}")


# ============================================================
# 分层抽样
# ============================================================

def _stratified_sample(instances: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
    """按能力类别等比例抽样。

    确保每个能力类别在样本中都有代表。
    """
    from app.benchmarks.locomo_adapter import get_ability

    # 按能力分组
    by_ability: Dict[str, List[Dict[str, Any]]] = {}
    for inst in instances:
        ability = get_ability(inst)
        if ability not in by_ability:
            by_ability[ability] = []
        by_ability[ability].append(inst)

    # 等比例分配
    total = len(instances)
    sampled: List[Dict[str, Any]] = []
    for ability, items in by_ability.items():
        # 该类别应抽样的数量
        n = max(1, round(limit * len(items) / total))
        n = min(n, len(items))
        sampled.extend(items[:n])

    # 若抽样后超过 limit，截断
    if len(sampled) > limit:
        sampled = sampled[:limit]

    return sampled


# ============================================================
# 竞品对比
# ============================================================

def _build_locomo_competitor_comparison(
    accuracy: float, ci_low: float, ci_high: float
) -> List[Dict[str, Any]]:
    """构建 LoCoMo 竞品对比表。

    数据来源：竞品对标报告（Zep 94.7%、Letta 74.0%）。
    """
    return [
        {"system": "AMS (本系统)", "accuracy": accuracy, "ci_low": ci_low, "ci_high": ci_high, "note": "P0+P1 优化"},
        {"system": "Zep", "accuracy": 0.947, "ci_low": None, "ci_high": None, "note": "LoCoMo 官方"},
        {"system": "Letta", "accuracy": 0.740, "ci_low": None, "ci_high": None, "note": "LoCoMo 官方"},
        {"system": "Mem0", "accuracy": None, "ci_low": None, "ci_high": None, "note": "未发布 LoCoMo"},
    ]
