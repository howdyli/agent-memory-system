"""
LLM 非确定性稳定性评测套件（L2）

量化并控制 LLM 非确定性对基准评分的影响，使发布的评分可信。

方法：
    - 对同一组 50 题（合成数据抽样），用相同配置运行 10 次
    - 统计：
        - 准确率均值/标准差/极差
        - 逐题稳定性（10 次中答对次数分布）
        - 不稳定题分析（3-7 次之间波动的题）

目标：10 次运行标准差 < 10%
"""
from __future__ import annotations

import logging
import statistics
from typing import Any, Dict, List, Optional

from app.benchmarks.base import BenchmarkResult, BenchmarkSuite, register_suite

logger = logging.getLogger(__name__)

# 目标：标准差 < 10%
TARGETS = {
    "stability_std_dev": 0.10,  # 实际指标应 < 0.10
    "stability_pass_rate": 0.80,  # 80% 的题稳定（10 次中 ≥8 次一致）
}


@register_suite
class LlmStabilitySuite(BenchmarkSuite):
    """L2 LLM 非确定性稳定性评测套件。

    用法：
        # 默认 50 题 × 10 次
        python -m app.benchmarks.runner run --suite llm_stability --repeat 10

        # 快速验证 10 题 × 3 次
        python -m app.benchmarks.runner run --suite llm_stability --limit 10 --repeat 3
    """

    name = "llm_stability"
    level = "L2"
    requires_llm = True
    requires_external = False
    description = "LLM 非确定性稳定性（10 次运行标准差 + 逐题稳定性分布）"

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self.repeat: int = self.config.get("repeat", 10)
        self.limit: int = self.config.get("limit", 50)
        self.user_id: int = self.config.get("user_id", 999)
        self.workspace_id: Optional[int] = self.config.get("workspace_id")
        self.use_llm_judge: bool = self.config.get("use_llm_judge", True)
        self.use_llm_answer: bool = self.config.get("use_llm_answer", True)
        self.top_k: int = self.config.get("top_k", 10)

    def setup(self) -> None:
        """加载合成数据集（稳定性测试不依赖真实数据集）。"""
        from app.benchmarks.sample_data import build_sample_dataset

        instances = build_sample_dataset()
        # 若合成数据不足 50 条，用全部
        if self.limit > 0 and self.limit < len(instances):
            self.instances = instances[:self.limit]
        else:
            self.instances = instances
        logger.info(f"稳定性评测：{len(self.instances)} 题 × {self.repeat} 次")

    def run(self) -> Dict[str, Any]:
        """对同一组题运行 N 次，收集每次的准确率与逐题正确性。"""
        from app.benchmarks.runner import run_benchmark

        run_results: List[Dict[str, Any]] = []
        run_accuracies: List[float] = []
        # 逐题正确性矩阵：question_id → [correct_run1, correct_run2, ...]
        per_question_correctness: Dict[str, List[bool]] = {}

        for run_idx in range(self.repeat):
            logger.info(f"=== 稳定性运行 {run_idx + 1}/{self.repeat} ===")
            result = run_benchmark(
                instances=self.instances,
                user_id=self.user_id,
                workspace_id=self.workspace_id,
                use_llm_judge=self.use_llm_judge,
                use_llm_answer=self.use_llm_answer,
                top_k_recall=self.top_k,
                repeat=1,  # 每次 run 不重复，外层控制重复次数
            )

            accuracy = result.get("metrics", {}).get("accuracy", 0.0)
            run_accuracies.append(accuracy)
            run_results.append({
                "run_idx": run_idx + 1,
                "accuracy": accuracy,
                "correct": result.get("metrics", {}).get("correct", 0),
                "total": result.get("metrics", {}).get("total", 0),
            })

            # 收集逐题正确性
            for r in result.get("results", []):
                qid = r.get("question_id", "")
                if qid not in per_question_correctness:
                    per_question_correctness[qid] = []
                per_question_correctness[qid].append(r.get("correct", False))

        return {
            "run_results": run_results,
            "run_accuracies": run_accuracies,
            "per_question_correctness": per_question_correctness,
        }

    def evaluate(self, raw_results: Dict[str, Any]) -> BenchmarkResult:
        """计算稳定性指标。"""
        run_accuracies = raw_results.get("run_accuracies", [])
        per_question = raw_results.get("per_question_correctness", {})

        if not run_accuracies:
            return BenchmarkResult(
                suite_name=self.name,
                level=self.level,
                metrics={"stability_std_dev": 1.0, "stability_pass_rate": 0.0},
                targets=TARGETS,
                passed=False,
                details=[{"error": "无运行结果"}],
                raw=raw_results,
            )

        # 准确率统计
        mean_acc = statistics.mean(run_accuracies)
        std_dev = statistics.stdev(run_accuracies) if len(run_accuracies) > 1 else 0.0
        min_acc = min(run_accuracies)
        max_acc = max(run_accuracies)
        range_acc = max_acc - min_acc

        # 逐题稳定性
        stable_count = 0  # 10 次中 ≥8 次一致（全对或全错）
        unstable_questions: List[Dict[str, Any]] = []
        for qid, correctness in per_question.items():
            correct_times = sum(correctness)
            total_runs = len(correctness)
            # 稳定定义：≥80% 的运行给出相同结果
            stable_ratio = max(correct_times, total_runs - correct_times) / total_runs
            if stable_ratio >= 0.8:
                stable_count += 1
            else:
                # 不稳定题：3-7 次之间波动
                unstable_questions.append({
                    "question_id": qid,
                    "correct_times": correct_times,
                    "total_runs": total_runs,
                    "stability": round(correct_times / total_runs, 2),
                })

        stability_pass_rate = stable_count / len(per_question) if per_question else 0.0

        metrics = {
            "stability_std_dev": std_dev,
            "stability_pass_rate": stability_pass_rate,
        }

        details = [{
            "run_count": len(run_accuracies),
            "accuracy_mean": round(mean_acc, 4),
            "accuracy_std_dev": round(std_dev, 4),
            "accuracy_min": round(min_acc, 4),
            "accuracy_max": round(max_acc, 4),
            "accuracy_range": round(range_acc, 4),
            "stable_questions": stable_count,
            "unstable_questions": len(unstable_questions),
            "total_questions": len(per_question),
            "unstable_details": unstable_questions[:10],  # 仅前 10 条
            "per_run": raw_results.get("run_results", []),
        }]

        result = BenchmarkResult(
            suite_name=self.name,
            level=self.level,
            metrics=metrics,
            targets=TARGETS,
            details=details,
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
