"""
LongMemEval 基准评测套件（L2）

支持两种模式：
    1. 合成数据（快速验证，无 LLM）：--sample 参数
    2. 真实 LongMemEval-S 数据集（对外发布，需 LLM）：--data 参数

特性：
    - 多数票机制（--repeat 3）：每题跑 3 次，取多数票消除 LLM 非确定性
    - Wilson 置信区间：准确率附带 95% CI
    - 能力分类统计：信息提取/多会话推理/时间推理/知识更新/弃权
    - 竞品对比表：与 Mem0/Zep/Letta 对比

数据集下载：
    wget https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/resolve/main/longmemeval_s_cleaned.json
    放置到 backend/app/benchmarks/data/longmemeval_s_cleaned.json
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from app.benchmarks.base import BenchmarkResult, BenchmarkSuite, register_suite

logger = logging.getLogger(__name__)

# 默认数据集路径
_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
_DEFAULT_DATA_PATH = os.path.join(_DATA_DIR, "longmemeval_s_cleaned.json")

# 目标：准确率 ≥ 70%（对外发布目标）
TARGETS = {
    "accuracy": 0.70,
}


@register_suite
class LongMemEvalSuite(BenchmarkSuite):
    """L2 LongMemEval 基准评测套件。

    用法：
        # 合成数据快速验证（无 LLM）
        python -m app.benchmarks.runner run --suite longmemeval \\
            --no-llm-judge --no-llm-answer

        # 真实数据集（需 LLM + 下载数据）
        python -m app.benchmarks.runner run --suite longmemeval \\
            --data app/benchmarks/data/longmemeval_s_cleaned.json \\
            --repeat 3

        # 限量测试（先 100 题）
        python -m app.benchmarks.runner run --suite longmemeval \\
            --data path/to/data --limit 100 --repeat 3
    """

    name = "longmemeval"
    level = "L2"
    requires_llm = True
    requires_external = False  # 数据集本地即可，不需外部服务
    description = "LongMemEval-S 端到端准确率（多数票 + Wilson CI + 竞品对比）"

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self.data_path: Optional[str] = self.config.get("data_path")
        self.use_sample: bool = self.config.get("use_sample", not self.data_path)
        self.limit: int = self.config.get("limit", 0)
        self.repeat: int = self.config.get("repeat", 1)
        self.user_id: int = self.config.get("user_id", 999)
        self.workspace_id: Optional[int] = self.config.get("workspace_id")
        self.use_llm_judge: bool = self.config.get("use_llm_judge", True)
        self.use_llm_answer: bool = self.config.get("use_llm_answer", True)
        self.top_k: int = self.config.get("top_k", 10)
        self.instances: List[Dict[str, Any]] = []

    def setup(self) -> None:
        """加载数据集。"""
        if self.use_sample or not self.data_path:
            from app.benchmarks.sample_data import build_sample_dataset
            self.instances = build_sample_dataset()
            logger.info(f"使用合成样本数据集，共 {len(self.instances)} 条实例")
        else:
            if not os.path.exists(self.data_path):
                # 尝试默认路径
                if os.path.exists(_DEFAULT_DATA_PATH):
                    self.data_path = _DEFAULT_DATA_PATH
                else:
                    raise FileNotFoundError(
                        f"LongMemEval 数据集未找到: {self.data_path}\n"
                        f"下载: wget https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/"
                        f"resolve/main/longmemeval_s_cleaned.json -O {_DEFAULT_DATA_PATH}"
                    )
            from app.benchmarks.longmemeval_adapter import load_dataset
            self.instances = load_dataset(self.data_path)
            logger.info(f"加载真实 LongMemEval-S 数据集: {self.data_path}，共 {len(self.instances)} 条实例")

        # 限量
        if self.limit > 0 and self.limit < len(self.instances):
            self.instances = self.instances[:self.limit]
            logger.info(f"限制为前 {self.limit} 条实例")

    def run(self) -> Dict[str, Any]:
        """运行 LongMemEval 基准测试。"""
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
        return results

    def evaluate(self, raw_results: Dict[str, Any]) -> BenchmarkResult:
        """提取准确率与 Wilson CI，判定是否达标。"""
        metrics = raw_results.get("metrics", {})
        accuracy = metrics.get("accuracy", 0.0)
        ci_low = metrics.get("accuracy_ci_low", 0.0)
        ci_high = metrics.get("accuracy_ci_high", 0.0)

        # 能力分类详情
        by_ability = metrics.get("by_ability", {})

        # 竞品对比
        competitor_comparison = _build_competitor_comparison(accuracy, ci_low, ci_high)

        # 稳定性（多数票模式下）
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


def _build_competitor_comparison(accuracy: float, ci_low: float, ci_high: float) -> List[Dict[str, Any]]:
    """构建竞品对比表（数据来源：竞品对标报告）。"""
    return [
        {"system": "AMS (本系统)", "accuracy": accuracy, "ci_low": ci_low, "ci_high": ci_high, "note": "P0+P1 优化"},
        {"system": "Mem0", "accuracy": 0.490, "ci_low": None, "ci_high": None, "note": "LongMemEval-S 官方"},
        {"system": "Zep", "accuracy": 0.712, "ci_low": None, "ci_high": None, "note": "LongMemEval-S 官方（部分配置 94.7%）"},
        {"system": "Letta", "accuracy": None, "ci_low": None, "ci_high": None, "note": "未发布 LongMemEval-S"},
    ]
