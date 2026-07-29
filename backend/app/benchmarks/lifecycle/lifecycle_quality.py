"""
生命周期与遗忘质量评测套件

验证差异化半衰期、智能遗忘、冲突检测这三个差异化能力确实有效。

评测项：
    1. 半衰期衰减正确性：误差 < 1e-6
    2. 遗忘精度：高重要性记忆误删率 < 5%
    3. 冲突检测准确率：Precision ≥ 0.85, Recall ≥ 0.80
    4. 过期清理：过期 100% 清理，未过期 0% 误删

使用 freezegun 模拟时间流逝。
"""
from __future__ import annotations

import json
import logging
import math
import os
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from app.benchmarks.base import BenchmarkResult, BenchmarkSuite, register_suite

logger = logging.getLogger(__name__)

# 固件路径
_FIXTURES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "fixtures")
_CONFLICT_FIXTURE_PATH = os.path.join(_FIXTURES_DIR, "conflict_pairs.json")

# 目标
TARGETS = {
    "half_life_accuracy": 1.0,        # 半衰期测试通过率
    "forgetting_precision": 0.95,     # 遗忘精度（高重要性误删率 < 5%）
    "conflict_precision": 0.85,       # 冲突检测准确率
    "conflict_recall": 0.80,          # 冲突检测召回率
    "expiry_cleanup_rate": 1.0,       # 过期清理完成率
    "expiry_misdelete_rate": 0.0,     # 过期误删率（应为 0）
}


@register_suite
class LifecycleQualitySuite(BenchmarkSuite):
    """L1 生命周期与遗忘质量评测套件。"""

    name = "lifecycle_quality"
    level = "L1"
    requires_llm = False
    requires_external = False
    description = "生命周期与遗忘质量（半衰期/遗忘精度/冲突检测/过期清理）"

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self._run_id = uuid.uuid4().hex[:8]
        self.user_id: int = 9201
        self.workspace_id: Optional[int] = self.config.get("workspace_id")
        self.conflict_fixtures: List[Dict[str, Any]] = []

    def setup(self) -> None:
        """加载冲突检测固件。"""
        if os.path.exists(_CONFLICT_FIXTURE_PATH):
            with open(_CONFLICT_FIXTURE_PATH, "r", encoding="utf-8") as f:
                self.conflict_fixtures = json.load(f)
            logger.info(f"加载 {len(self.conflict_fixtures)} 对冲突检测固件")
        else:
            logger.warning(f"冲突固件不存在: {_CONFLICT_FIXTURE_PATH}，使用默认固件")
            self.conflict_fixtures = _default_conflict_pairs()

    def run(self) -> Dict[str, Any]:
        """执行 4 项生命周期评测。"""
        results: Dict[str, Any] = {}

        results["half_life"] = self._test_half_life_decay()
        results["forgetting"] = self._test_forgetting_precision()
        results["conflict"] = self._test_conflict_detection()
        results["expiry"] = self._test_expiry_cleanup()

        return results

    # ============================================================
    # 评测项 1：半衰期衰减正确性
    # ============================================================

    def _test_half_life_decay(self) -> Dict[str, Any]:
        """验证 decay_score 与公式 2^(-days/half_life) 误差 < 1e-6。

        测试 info(永久)/plan(90d)/preference(1d) 三种类型的衰减。
        """
        from app.services.memory_lifecycle_service import calculate_decay_score

        test_cases = [
            # (half_life_days, elapsed_days, expected_decay)
            (None, 100, 1.0),           # 永久：不衰减
            (90, 0, 1.0),               # 90 天半衰期，0 天：满值
            (90, 90, 0.5),              # 90 天半衰期，90 天：半值
            (90, 180, 0.25),            # 90 天半衰期，180 天：1/4
            (1, 0, 1.0),                # 1 天半衰期，0 天：满值
            (1, 1, 0.5),                # 1 天半衰期，1 天：半值
            (1, 3, 0.125),              # 1 天半衰期，3 天：1/8
            (30, 30, 0.5),              # 30 天半衰期，30 天：半值
        ]

        passed_count = 0
        details = []
        for half_life, elapsed, expected in test_cases:
            created_at = datetime.now() - timedelta(days=elapsed)
            actual = calculate_decay_score(created_at, half_life)

            # 允许微小浮点误差
            is_pass = abs(actual - expected) < 1e-6
            if is_pass:
                passed_count += 1

            details.append({
                "half_life_days": half_life,
                "elapsed_days": elapsed,
                "expected": expected,
                "actual": round(actual, 6),
                "passed": is_pass,
            })

        pass_rate = passed_count / len(test_cases)
        return {
            "pass_rate": pass_rate,
            "passed_count": passed_count,
            "total": len(test_cases),
            "details": details,
        }

    # ============================================================
    # 评测项 2：遗忘精度
    # ============================================================

    def _test_forgetting_precision(self) -> Dict[str, Any]:
        """验证高重要性记忆误删率 < 5%。

        策略：创建 20 高重要性 + 30 低重要性片段，
        触发 recalculate_importance，检查被标记为 cold 的集合。
        """
        from app.services.memory_fragment_service import create_fragment
        from app.services.smart_forgetting_service import recalculate_importance
        from app.core.db_client import get_db_client

        # 清理旧数据
        db = get_db_client()
        db.execute(
            "DELETE FROM memory_fragments WHERE user_id = ?",
            (self.user_id,),
        )

        high_ids: List[int] = []
        low_ids: List[int] = []

        try:
            # 创建 20 条高重要性片段
            for i in range(20):
                result = create_fragment(
                    user_id=self.user_id,
                    content=f"高重要性片段 {i} [run={self._run_id}]",
                    fragment_type="info",
                    importance_score=0.9,  # 高重要性
                    workspace_id=self.workspace_id,
                )
                frag_id = result.get("id") if isinstance(result, dict) else result
                if frag_id:
                    high_ids.append(int(frag_id))

            # 创建 30 条低重要性片段
            for i in range(30):
                result = create_fragment(
                    user_id=self.user_id,
                    content=f"低重要性片段 {i} [run={self._run_id}]",
                    fragment_type="preference",
                    importance_score=0.1,  # 低重要性
                    workspace_id=self.workspace_id,
                )
                frag_id = result.get("id") if isinstance(result, dict) else result
                if frag_id:
                    low_ids.append(int(frag_id))

            # 触发重算（auto_forget=True，阈值设高以便低重要性被标记冷）
            recalc_result = recalculate_importance(
                user_id=self.user_id,
                workspace_id=self.workspace_id,
                forget_threshold=0.5,
                auto_forget=True,
            )

            # 查询被标记为 cold 的片段
            cold_rows = db.execute(
                "SELECT id FROM memory_fragments WHERE user_id = ? AND lifecycle_status = 'cold'",
                (self.user_id,),
            ).fetchall()
            cold_ids = {row[0] for row in cold_rows} if cold_rows else set()

            # 计算误删率（高重要性被标记为 cold 的比例）
            high_cold = sum(1 for fid in high_ids if fid in cold_ids)
            low_cold = sum(1 for fid in low_ids if fid in cold_ids)

            misdelete_rate = high_cold / len(high_ids) if high_ids else 0.0
            precision = 1.0 - misdelete_rate

            return {
                "precision": precision,
                "misdelete_rate": misdelete_rate,
                "high_total": len(high_ids),
                "high_cold": high_cold,
                "low_total": len(low_ids),
                "low_cold": low_cold,
                "recalc_result": recalc_result,
            }
        finally:
            # 清理
            db.execute(
                "DELETE FROM memory_fragments WHERE user_id = ?",
                (self.user_id,),
            )

    # ============================================================
    # 评测项 3：冲突检测准确率
    # ============================================================

    def _test_conflict_detection(self) -> Dict[str, Any]:
        """验证冲突检测 Precision ≥ 0.85, Recall ≥ 0.80。

        使用 50 对固件（25 冲突 + 25 不冲突），跑 contradiction_service。
        """
        from app.services.contradiction_service import detect_contradiction
        from app.services.memory_fragment_service import create_fragment
        from app.core.db_client import get_db_client

        db = get_db_client()
        db.execute(
            "DELETE FROM memory_fragments WHERE user_id = ?",
            (self.user_id,),
        )

        try:
            true_positives = 0   # 正确检测到冲突
            false_positives = 0  # 误报（不冲突被判为冲突）
            false_negatives = 0  # 漏报（冲突未检测到）
            true_negatives = 0   # 正确判断不冲突
            details = []

            for pair in self.conflict_fixtures:
                fragment_a = pair["fragment_a"]
                fragment_b = pair["fragment_b"]
                is_conflict = pair["is_conflict"]

                # 摄入 fragment_a
                create_fragment(
                    user_id=self.user_id,
                    content=fragment_a,
                    fragment_type="info",
                    importance_score=0.5,
                    workspace_id=self.workspace_id,
                )

                # 检测 fragment_b 是否与 fragment_a 冲突
                try:
                    result = detect_contradiction(
                        user_id=self.user_id,
                        new_content=fragment_b,
                        workspace_id=self.workspace_id,
                        enable_semantic=True,
                    )
                    detected = len(result.get("contradictions", [])) > 0
                except Exception as e:
                    logger.debug(f"冲突检测异常: {e}")
                    detected = False

                # 统计
                if is_conflict and detected:
                    true_positives += 1
                elif is_conflict and not detected:
                    false_negatives += 1
                elif not is_conflict and detected:
                    false_positives += 1
                else:
                    true_negatives += 1

                details.append({
                    "fragment_a": fragment_a[:50],
                    "fragment_b": fragment_b[:50],
                    "expected_conflict": is_conflict,
                    "detected": detected,
                    "correct": (is_conflict == detected),
                })

                # 清理当前 pair 的数据，避免下一条受影响
                db.execute(
                    "DELETE FROM memory_fragments WHERE user_id = ?",
                    (self.user_id,),
                )

            precision = true_positives / max(true_positives + false_positives, 1)
            recall = true_positives / max(true_positives + false_negatives, 1)

            return {
                "precision": precision,
                "recall": recall,
                "true_positives": true_positives,
                "false_positives": false_positives,
                "false_negatives": false_negatives,
                "true_negatives": true_negatives,
                "total": len(self.conflict_fixtures),
                "details": details,
            }
        finally:
            db.execute(
                "DELETE FROM memory_fragments WHERE user_id = ?",
                (self.user_id,),
            )

    # ============================================================
    # 评测项 4：过期清理
    # ============================================================

    def _test_expiry_cleanup(self) -> Dict[str, Any]:
        """验证过期记忆 100% 清理，未过期 0% 误删。

        策略：创建带 TTL 的记忆，模拟过期，调用 cleanup_expired_memories。
        """
        from app.services.memory_variable_service import set_memory_variable
        from app.services.memory_lifecycle_service import cleanup_expired_memories
        from app.core.db_client import get_db_client

        db = get_db_client()
        # 清理旧数据
        db.execute(
            "DELETE FROM memory_variables WHERE user_id = ?",
            (self.user_id,),
        )

        try:
            # 创建 10 条"已过期"变量（TTL=1 秒，等待过期）
            expired_keys = [f"expired_{i}_{self._run_id}" for i in range(10)]
            for key in expired_keys:
                set_memory_variable(
                    user_id=self.user_id, key=key, value="expired",
                    ttl=1, workspace_id=self.workspace_id,  # 1 秒 TTL
                )

            # 创建 10 条"未过期"变量（TTL=3600 秒）
            active_keys = [f"active_{i}_{self._run_id}" for i in range(10)]
            for key in active_keys:
                set_memory_variable(
                    user_id=self.user_id, key=key, value="active",
                    ttl=3600, workspace_id=self.workspace_id,
                )

            # 等待 2 秒让 expired 过期
            import time
            time.sleep(2)

            # 触发清理
            cleanup_result = cleanup_expired_memories(workspace_id=self.workspace_id)

            # 检查过期变量是否被清理
            from app.services.memory_variable_service import get_memory_variable
            expired_cleaned = 0
            for key in expired_keys:
                val = get_memory_variable(
                    user_id=self.user_id, key=key, workspace_id=self.workspace_id,
                )
                if val is None:
                    expired_cleaned += 1

            # 检查未过期变量是否仍存在
            active_remaining = 0
            for key in active_keys:
                val = get_memory_variable(
                    user_id=self.user_id, key=key, workspace_id=self.workspace_id,
                )
                if val is not None:
                    active_remaining += 1

            cleanup_rate = expired_cleaned / len(expired_keys)
            misdelete_rate = (len(active_keys) - active_remaining) / len(active_keys)

            return {
                "cleanup_rate": cleanup_rate,
                "misdelete_rate": misdelete_rate,
                "expired_total": len(expired_keys),
                "expired_cleaned": expired_cleaned,
                "active_total": len(active_keys),
                "active_remaining": active_remaining,
                "cleanup_result": cleanup_result,
            }
        finally:
            db.execute(
                "DELETE FROM memory_variables WHERE user_id = ?",
                (self.user_id,),
            )

    # ============================================================
    # 评估
    # ============================================================

    def evaluate(self, raw_results: Dict[str, Any]) -> BenchmarkResult:
        """汇总 4 项评测指标。"""
        half_life = raw_results.get("half_life", {})
        forgetting = raw_results.get("forgetting", {})
        conflict = raw_results.get("conflict", {})
        expiry = raw_results.get("expiry", {})

        metrics = {
            "half_life_accuracy": half_life.get("pass_rate", 0.0),
            "forgetting_precision": forgetting.get("precision", 0.0),
            "conflict_precision": conflict.get("precision", 0.0),
            "conflict_recall": conflict.get("recall", 0.0),
            "expiry_cleanup_rate": expiry.get("cleanup_rate", 0.0),
            "expiry_misdelete_rate": expiry.get("misdelete_rate", 1.0),
        }

        details = [
            {"test": "half_life", **half_life},
            {"test": "forgetting", **forgetting},
            {"test": "conflict", **conflict},
            {"test": "expiry", **expiry},
        ]

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
            db.execute(
                "DELETE FROM memory_fragments WHERE user_id = ?",
                (self.user_id,),
            )
            db.execute(
                "DELETE FROM memory_variables WHERE user_id = ?",
                (self.user_id,),
            )
        except Exception as e:
            logger.debug(f"清理失败: {e}")


# ============================================================
# 默认冲突检测固件（固件文件不存在时降级使用）
# ============================================================

def _default_conflict_pairs() -> List[Dict[str, Any]]:
    """默认冲突检测固件（最小验证集，10 对）。"""
    return [
        # 5 对冲突
        {
            "fragment_a": "我现在住在纽约",
            "fragment_b": "我最近搬到了洛杉矶",
            "is_conflict": True,
            "reason": "地点变更",
        },
        {
            "fragment_a": "我的职位是数据分析师",
            "fragment_b": "我现在是软件工程师了",
            "is_conflict": True,
            "reason": "职位变更",
        },
        {
            "fragment_a": "我最喜欢的编程语言是 Java",
            "fragment_b": "我现在最喜欢 Python",
            "is_conflict": True,
            "reason": "偏好变更",
        },
        {
            "fragment_a": "我在 A 公司工作",
            "fragment_b": "我跳槽到了 B 公司",
            "is_conflict": True,
            "reason": "组织变更",
        },
        {
            "fragment_a": "我的状态是单身",
            "fragment_b": "我现在已婚",
            "is_conflict": True,
            "reason": "状态变更",
        },
        # 5 对不冲突
        {
            "fragment_a": "我喜欢吃中餐",
            "fragment_b": "我也喜欢日料",
            "is_conflict": False,
            "reason": "并列偏好",
        },
        {
            "fragment_a": "我会 Python",
            "fragment_b": "我在学 Rust",
            "is_conflict": False,
            "reason": "技能扩展",
        },
        {
            "fragment_a": "我去过东京",
            "fragment_b": "我也去过巴黎",
            "is_conflict": False,
            "reason": "并列经历",
        },
        {
            "fragment_a": "我的名字是张三",
            "fragment_b": "我今年 28 岁",
            "is_conflict": False,
            "reason": "不同属性",
        },
        {
            "fragment_a": "我喜欢打篮球",
            "fragment_b": "我周末经常打篮球",
            "is_conflict": False,
            "reason": "同一事实补充",
        },
    ]
