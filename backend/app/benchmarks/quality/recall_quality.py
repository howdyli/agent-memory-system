"""
召回质量评测套件

用标准 IR（信息检索）指标量化召回质量，对比三条召回路径：
    1. 标准召回（memory_fragment_service.search_fragments_by_semantic）
    2. P0 advanced_recall
    3. P1 advanced_recall_v2

指标：
    - Precision@5: 前 5 结果中相关片段占比
    - Recall@10: 期望片段在前 10 中被召回比例
    - F1@10: P/R 调和平均
    - MRR: 第一个相关片段的倒数排名
    - NDCG@10: 考虑排序位置的归一化折损累计增益

数据来源：
    - fixtures/recall_ground_truth.json（人工标注的 ground truth）
    - 复用 sample_data.py 的合成数据作为部分标注源
"""
from __future__ import annotations

import json
import logging
import math
import os
from typing import Any, Dict, List, Optional, Set

from app.benchmarks.base import BenchmarkResult, BenchmarkSuite, register_suite

logger = logging.getLogger(__name__)

# 固件目录
_FIXTURES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "fixtures")
_GROUND_TRUTH_PATH = os.path.join(_FIXTURES_DIR, "recall_ground_truth.json")

# 指标目标（来自设计文档第 3.1 节）
TARGETS = {
    "precision_at_5": 0.80,
    "recall_at_10": 0.90,
    "f1_at_10": 0.85,
    "mrr": 0.70,
    "ndcg_at_10": 0.75,
}


# ============================================================
# IR 指标计算
# ============================================================

def precision_at_k(retrieved_ids: List[str], relevant_ids: Set[str], k: int = 5) -> float:
    """Precision@K: 前 K 结果中相关片段占比。"""
    if k <= 0:
        return 0.0
    top_k = retrieved_ids[:k]
    if not top_k:
        return 0.0
    relevant_hits = sum(1 for rid in top_k if rid in relevant_ids)
    return relevant_hits / len(top_k)


def recall_at_k(retrieved_ids: List[str], relevant_ids: Set[str], k: int = 10) -> float:
    """Recall@K: 期望片段在前 K 中被召回比例。"""
    if not relevant_ids:
        return 0.0
    top_k = retrieved_ids[:k]
    relevant_hits = sum(1 for rid in top_k if rid in relevant_ids)
    return relevant_hits / len(relevant_ids)


def f1_at_k(precision: float, recall: float) -> float:
    """F1@K: P/R 调和平均。"""
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def mean_reciprocal_rank(retrieved_ids: List[str], relevant_ids: Set[str]) -> float:
    """MRR: 第一个相关片段的倒数排名。"""
    for i, rid in enumerate(retrieved_ids, start=1):
        if rid in relevant_ids:
            return 1.0 / i
    return 0.0


def ndcg_at_k(retrieved_ids: List[str], relevant_ids: Set[str], k: int = 10) -> float:
    """NDCG@K: 考虑排序位置的归一化折损累计增益。

    DCG = sum(rel_i / log2(i+1)) for i in 1..k
    IDCG = 理想排序下的 DCG
    NDCG = DCG / IDCG
    """
    if not relevant_ids:
        return 0.0

    # DCG
    dcg = 0.0
    for i, rid in enumerate(retrieved_ids[:k], start=1):
        rel = 1.0 if rid in relevant_ids else 0.0
        dcg += rel / math.log2(i + 1)

    # IDCG（理想排序：所有相关项排在最前）
    ideal_hits = min(len(relevant_ids), k)
    idcg = sum(1.0 / math.log2(i + 1) for i in range(1, ideal_hits + 1))

    if idcg == 0:
        return 0.0
    return dcg / idcg


def compute_all_metrics(
    retrieved_ids: List[str], relevant_ids: Set[str]
) -> Dict[str, float]:
    """计算全部 IR 指标。"""
    p5 = precision_at_k(retrieved_ids, relevant_ids, k=5)
    r10 = recall_at_k(retrieved_ids, relevant_ids, k=10)
    return {
        "precision_at_5": p5,
        "recall_at_10": r10,
        "f1_at_10": f1_at_k(p5, r10),
        "mrr": mean_reciprocal_rank(retrieved_ids, relevant_ids),
        "ndcg_at_10": ndcg_at_k(retrieved_ids, relevant_ids, k=10),
    }


# ============================================================
# Ground Truth 加载
# ============================================================

def load_ground_truth(path: str = _GROUND_TRUTH_PATH) -> List[Dict[str, Any]]:
    """加载召回质量标注固件。

    固件格式::

        [
            {
                "question_id": "rq_001",
                "question": "用户的项目有哪些？",
                "expected_fragment_ids": ["frag_abc", "frag_def"],
                "capability": "multi_session",
                "haystack_sessions": [...],  # 可选，复用合成数据
                "haystack_dates": [...]
            }
        ]
    """
    if not os.path.exists(path):
        logger.warning(f"召回质量固件不存在: {path}，将使用合成数据推断")
        return _infer_ground_truth_from_sample()
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _infer_ground_truth_from_sample() -> List[Dict[str, Any]]:
    """从合成数据推断 ground truth（固件未就绪时的降级方案）。

    策略：用 sample_data 的 question + haystack_sessions 构建评测实例，
    expected_fragment_ids 留空（由运行时根据摄入内容推断）。
    """
    from app.benchmarks.sample_data import build_sample_dataset

    instances = build_sample_dataset()
    gt_list = []
    for inst in instances:
        gt_list.append({
            "question_id": inst["question_id"],
            "question": inst["question"],
            "capability": inst.get("question_type", "unknown"),
            "haystack_sessions": inst.get("haystack_sessions", []),
            "haystack_dates": inst.get("haystack_dates", []),
            "haystack_session_ids": inst.get("haystack_session_ids", []),
            "expected_fragment_contents": _extract_user_contents(inst),
        })
    return gt_list


def _extract_user_contents(inst: Dict[str, Any]) -> List[str]:
    """从实例的 haystack_sessions 中提取所有用户消息内容。

    用于在摄入后通过内容匹配找到对应的 fragment_id，作为 ground truth。
    """
    contents = []
    for session in inst.get("haystack_sessions", []):
        for turn in session:
            if turn.get("role") == "user":
                content = turn.get("content", "").strip()
                if content:
                    contents.append(content)
    return contents


# ============================================================
# 召回质量评测套件
# ============================================================

@register_suite
class RecallQualitySuite(BenchmarkSuite):
    """L1 召回质量评测套件。

    对每条 ground truth 实例：
        1. 摄入会话历史
        2. 通过内容匹配确定 ground truth fragment_ids
        3. 跑三条召回路径，收集返回的 fragment_ids
        4. 计算 IR 指标
        5. 汇总各路径的平均指标，判定是否达标
    """

    name = "recall_quality"
    level = "L1"
    requires_llm = False
    requires_external = False
    description = "召回质量 IR 指标（P@5/R@10/F1/MRR/NDCG），对比三条召回路径"

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self.user_id: int = self.config.get("user_id", 999)
        self.workspace_id: Optional[int] = self.config.get("workspace_id")
        self.top_k: int = self.config.get("top_k", 10)
        self.ground_truth: List[Dict[str, Any]] = []
        # 存储摄入后的内容→fragment_id 映射
        self.content_to_frag_id: Dict[str, int] = {}

    def setup(self) -> None:
        """加载 ground truth 固件。"""
        self.ground_truth = load_ground_truth()
        logger.info(f"加载 {len(self.ground_truth)} 条 ground truth 实例")

    def run(self) -> Dict[str, Any]:
        """对每条实例跑三条召回路径，收集结果。"""
        from app.benchmarks.longmemeval_adapter import MemoryAdapter

        paths_results: Dict[str, List[Dict[str, Any]]] = {
            "standard": [],
            "advanced_p0": [],
            "advanced_p1": [],
        }

        for i, gt in enumerate(self.ground_truth):
            qid = gt["question_id"]
            question = gt["question"]
            logger.info(f"[{i+1}/{len(self.ground_truth)}] 评测 {qid}: {question[:50]}")

            # 每条实例独立：重置 + 摄入
            adapter = MemoryAdapter(self.user_id, self.workspace_id)
            adapter.reset()
            sessions = gt.get("haystack_sessions", [])
            dates = gt.get("haystack_dates", [])
            ids = gt.get("haystack_session_ids", [])
            adapter.ingest_history(sessions, dates, ids)

            # 确定 ground truth fragment_ids
            gt_frag_ids = self._resolve_ground_truth_ids(gt, adapter)
            if not gt_frag_ids:
                logger.warning(f"  {qid} 无法确定 ground truth fragment_ids，跳过")
                continue

            # 三条召回路径
            retrieved = self._run_three_paths(question)
            for path_name, retrieved_ids in retrieved.items():
                metrics = compute_all_metrics(retrieved_ids, set(gt_frag_ids))
                paths_results[path_name].append({
                    "question_id": qid,
                    "question": question,
                    "gt_fragment_ids": gt_frag_ids,
                    "retrieved_ids": retrieved_ids,
                    "metrics": metrics,
                })
                logger.info(
                    f"  {path_name}: P@5={metrics['precision_at_5']:.2f} "
                    f"R@10={metrics['recall_at_10']:.2f} MRR={metrics['mrr']:.2f}"
                )

        return {"paths_results": paths_results, "instance_count": len(self.ground_truth)}

    def _resolve_ground_truth_ids(
        self, gt: Dict[str, Any], adapter: Any
    ) -> List[str]:
        """将 ground truth 的内容标注解析为实际 fragment_id 列表。

        策略：
        1. 若固件含 expected_fragment_ids，直接使用
        2. 否则用 expected_fragment_contents 内容匹配
        3. 若都没有，用该实例所有摄入的用户消息作为 ground truth（宽松匹配）
        """
        # 优先使用显式 ID
        explicit_ids = gt.get("expected_fragment_ids")
        if explicit_ids:
            return [str(fid) for fid in explicit_ids]

        # 内容匹配：查询当前用户的所有片段，找到内容匹配的
        from app.services.memory_fragment_service import list_fragments
        try:
            result = list_fragments(
                user_id=self.user_id,
                fragment_type=None,
                workspace_id=self.workspace_id,
            )
            all_frags = result.get("fragments", []) if isinstance(result, dict) else result
        except Exception as e:
            logger.debug(f"list_fragments 失败: {e}")
            return []

        # 期望内容列表
        expected_contents = gt.get("expected_fragment_contents", [])
        if not expected_contents:
            # 降级：所有摄入的片段都算 ground truth
            return [str(f.get("id")) for f in all_frags if f.get("id")]

        # 通过内容子串匹配
        matched_ids = []
        for frag in all_frags:
            frag_content = frag.get("content", "")
            for ec in expected_contents:
                # 双向子串匹配（摄入内容可能被 P1-1 分解或加日期前缀）
                if ec in frag_content or frag_content in ec:
                    matched_ids.append(str(frag.get("id")))
                    break
        return matched_ids

    def _run_three_paths(self, question: str) -> Dict[str, List[str]]:
        """对同一问题跑三条召回路径，返回各路径的 fragment_id 列表。"""
        paths: Dict[str, List[str]] = {}

        # 1. 标准召回（语义搜索）
        try:
            from app.services.memory_fragment_service import search_fragments_by_semantic
            result = search_fragments_by_semantic(
                user_id=self.user_id,
                query=question,
                top_k=self.top_k,
                threshold=0.0,  # 评测时降低阈值，看完整排序
                workspace_id=self.workspace_id,
            )
            frags = result.get("fragments", []) if isinstance(result, dict) else []
            paths["standard"] = [str(f.get("id")) for f in frags if f.get("id")]
        except Exception as e:
            logger.debug(f"标准召回失败: {e}")
            paths["standard"] = []

        # 2. P0 advanced_recall
        try:
            from app.services.advanced_recall import advanced_recall
            result = advanced_recall(
                user_id=self.user_id,
                question=question,
                top_k=self.top_k,
                workspace_id=self.workspace_id,
            )
            memories = result.get("memories", []) if isinstance(result, dict) else []
            paths["advanced_p0"] = [
                str(m.get("fragment_id") or m.get("id"))
                for m in memories
                if m.get("fragment_id") or m.get("id")
            ]
        except Exception as e:
            logger.debug(f"P0 advanced_recall 失败: {e}")
            paths["advanced_p0"] = []

        # 3. P1 advanced_recall_v2
        try:
            from app.services.advanced_recall import advanced_recall_v2
            result = advanced_recall_v2(
                user_id=self.user_id,
                question=question,
                top_k=self.top_k,
                workspace_id=self.workspace_id,
            )
            memories = result.get("memories", []) if isinstance(result, dict) else []
            paths["advanced_p1"] = [
                str(m.get("fragment_id") or m.get("id"))
                for m in memories
                if m.get("fragment_id") or m.get("id")
            ]
        except Exception as e:
            logger.debug(f"P1 advanced_recall_v2 失败: {e}")
            paths["advanced_p1"] = []

        return paths

    def evaluate(self, raw_results: Dict[str, Any]) -> BenchmarkResult:
        """汇总各路径的平均指标，判定是否达标。"""
        paths_results = raw_results.get("paths_results", {})

        # 以 P1 advanced_recall_v2（默认生产路径）作为主指标判定
        primary_path = "advanced_p1"
        primary_results = paths_results.get(primary_path, [])

        if not primary_results:
            return BenchmarkResult(
                suite_name=self.name,
                level=self.level,
                metrics={k: 0.0 for k in TARGETS},
                targets=TARGETS,
                passed=False,
                details=[{"error": "主路径无有效结果"}],
                raw=raw_results,
            )

        # 计算平均指标
        avg_metrics: Dict[str, float] = {}
        for metric_name in TARGETS:
            values = [r["metrics"][metric_name] for r in primary_results if metric_name in r.get("metrics", {})]
            avg_metrics[metric_name] = sum(values) / len(values) if values else 0.0

        result = BenchmarkResult(
            suite_name=self.name,
            level=self.level,
            metrics=avg_metrics,
            targets=TARGETS,
            details=[{
                "path": primary_path,
                "instance_count": len(primary_results),
                "per_instance": primary_results,
                "all_paths_summary": {
                    path: {
                        name: sum(r["metrics"][name] for r in results if name in r.get("metrics", {})) / max(len(results), 1)
                        for name in TARGETS
                    }
                    for path, results in paths_results.items()
                },
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
            try:
                db.execute("DELETE FROM memory_search_keys WHERE user_id = ?", (self.user_id,))
            except Exception:
                pass
        except Exception as e:
            logger.debug(f"清理失败: {e}")
