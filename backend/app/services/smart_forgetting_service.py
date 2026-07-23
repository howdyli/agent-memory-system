"""
智能遗忘机制（R-07）

核心能力：
1. **多因子重要性评分**：综合召回频率、时间衰减、证据强度、矛盾次数计算记忆重要性
2. **自动遗忘**：低重要性记忆自动降权或标记为冷记忆
3. **遗忘审计**：记录遗忘决策的原因和过程

重要性评分公式：
    importance = w1 * recall_factor + w2 * decay_factor + w3 * evidence_factor + w4 * contradiction_factor

    - recall_factor (权重 0.35)：基于 memory_trace_events 中的 recalled 事件次数
    - decay_factor (权重 0.25)：基于半衰期的指数衰减
    - evidence_factor (权重 0.30)：原始 importance_score（用户/系统设定的重要性）
    - contradiction_factor (权重 0.10)：被标记为 superseded 的记忆获得惩罚

设计原则：
- 非破坏性：仅降权和标记冷记忆，不直接删除
- 可配置：权重和阈值可通过参数调整
- 可审计：每次重算记录原因和结果
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from app.core.db_client import get_db_client
from app.services.memory_lifecycle_service import (
    calculate_decay_score,
    get_half_life,
    mark_cold,
)

logger = logging.getLogger(__name__)

# ============================================================
# 重要性评分权重配置
# ============================================================

DEFAULT_WEIGHTS = {
    "recall_frequency": 0.35,   # 召回频率（最重要）
    "time_decay": 0.25,         # 时间衰减
    "evidence": 0.30,           # 证据强度（原始 importance_score）
    "contradiction": 0.10,      # 矛盾惩罚
}

# 遗忘阈值：低于此分数的记忆将被标记为冷记忆
DEFAULT_FORGET_THRESHOLD = 0.15
# 召回次数归一化的分母（超过此次数的 recall_factor = 1.0）
DEFAULT_RECALL_NORMALIZATION = 10


# ============================================================
# 1. 单条记忆重要性评分
# ============================================================

def compute_importance_score(
    fragment: Dict[str, Any],
    recall_count: int = 0,
    is_superseded: bool = False,
    weights: Optional[Dict[str, float]] = None,
    recall_normalization: int = DEFAULT_RECALL_NORMALIZATION,
) -> Dict[str, Any]:
    """计算单条记忆的多因子重要性评分。

    Args:
        fragment: 记忆片段字典（需含 fragment_type, created_at, importance_score）
        recall_count: 该记忆被召回的次数
        is_superseded: 是否已被标记为 superseded
        weights: 各因子权重（为 None 则使用默认权重）
        recall_normalization: 召回次数归一化分母

    Returns:
        {
            "total_score": float,          # 综合重要性评分 (0.0 ~ 1.0)
            "factors": {
                "recall_frequency": float,  # 召回频率因子 (0.0 ~ 1.0)
                "time_decay": float,        # 时间衰减因子 (0.0 ~ 1.0)
                "evidence": float,          # 证据强度因子 (0.0 ~ 1.0)
                "contradiction": float,     # 矛盾因子 (0.0 ~ 1.0)
            },
            "weights": Dict[str, float],   # 使用的权重
        }
    """
    w = weights or DEFAULT_WEIGHTS

    # 因子 1: 召回频率（对数归一化，避免高频记忆主导）
    # recall_count=0 → 0.0, recall_count=10 → ~1.0, recall_count=100 → ~1.0
    if recall_count > 0:
        recall_factor = min(1.0, math.log(1 + recall_count) / math.log(1 + recall_normalization))
    else:
        recall_factor = 0.0

    # 因子 2: 时间衰减
    fragment_type = fragment.get("fragment_type", "info")
    half_life_days = get_half_life(fragment_type)
    decay_factor = calculate_decay_score(fragment.get("created_at"), half_life_days)

    # 因子 3: 证据强度（原始 importance_score）
    evidence_factor = float(fragment.get("importance_score") or 0.5)
    evidence_factor = max(0.0, min(1.0, evidence_factor))

    # 因子 4: 矛盾惩罚（被 superseded 的记忆获得 0 分，否则 1.0）
    contradiction_factor = 0.0 if is_superseded else 1.0

    # 加权综合
    total_score = (
        w["recall_frequency"] * recall_factor
        + w["time_decay"] * decay_factor
        + w["evidence"] * evidence_factor
        + w["contradiction"] * contradiction_factor
    )
    total_score = max(0.0, min(1.0, total_score))

    return {
        "total_score": round(total_score, 4),
        "factors": {
            "recall_frequency": round(recall_factor, 4),
            "time_decay": round(decay_factor, 4),
            "evidence": round(evidence_factor, 4),
            "contradiction": round(contradiction_factor, 4),
        },
        "weights": w,
    }


# ============================================================
# 2. 批量重要性重算
# ============================================================

def recalculate_importance(
    user_id: Optional[int] = None,
    workspace_id: Optional[int] = None,
    weights: Optional[Dict[str, float]] = None,
    forget_threshold: float = DEFAULT_FORGET_THRESHOLD,
    auto_forget: bool = True,
    batch_size: int = 500,
) -> Dict[str, Any]:
    """批量重算记忆重要性，并可选自动遗忘低重要性记忆。

    扫描所有 active 状态的记忆片段，基于多因子评分公式重新计算 importance_score。
    低于 forget_threshold 的记忆将被标记为冷记忆（如果 auto_forget=True）。

    Args:
        user_id: 指定用户（None = 所有用户）
        workspace_id: workspace ID
        weights: 自定义权重
        forget_threshold: 遗忘阈值（低于此值的记忆将被降级）
        auto_forget: 是否自动将低重要性记忆标记为冷记忆
        batch_size: 批量处理大小

    Returns:
        {
            "success": bool,
            "total_evaluated": int,      # 评估的记忆总数
            "total_forgotten": int,      # 被遗忘（标记冷）的记忆数
            "total_downweighted": int,   # 被降权的记忆数
            "average_score": float,      # 平均重要性评分
            "score_distribution": Dict,  # 评分分布
        }
    """
    try:
        db = get_db_client()
        w = weights or DEFAULT_WEIGHTS

        # 查询所有 active 记忆
        conditions = ["lifecycle_status = 'active'"]
        params: List[Any] = []
        if user_id is not None:
            conditions.append("user_id = ?")
            params.append(user_id)

        fragments = db.execute(
            f"""SELECT id, user_id, fragment_type, content, importance_score,
                      created_at, lifecycle_status
               FROM memory_fragments
               WHERE {' AND '.join(conditions)}
               ORDER BY created_at ASC
               LIMIT ?""",
            (*params, batch_size),
        )

        if not fragments:
            return {
                "success": True,
                "total_evaluated": 0,
                "total_forgotten": 0,
                "total_downweighted": 0,
                "average_score": 0.0,
                "score_distribution": {},
            }

        # 批量获取召回次数
        fragment_ids = [f["id"] for f in fragments]
        recall_counts = _batch_get_recall_counts(fragment_ids)

        # 批量获取 superseded 状态
        superseded_ids = _get_superseded_ids(
            user_id=fragments[0]["user_id"] if fragments else None,
        )

        total_evaluated = 0
        total_forgotten = 0
        total_downweighted = 0
        score_sum = 0.0
        score_ranges = {"0.0-0.2": 0, "0.2-0.4": 0, "0.4-0.6": 0, "0.6-0.8": 0, "0.8-1.0": 0}

        for frag in fragments:
            frag = dict(frag)  # sqlite3.Row → dict
            frag_id = frag["id"]
            recall_count = recall_counts.get(frag_id, 0)
            is_superseded = frag_id in superseded_ids

            result = compute_importance_score(
                fragment=frag,
                recall_count=recall_count,
                is_superseded=is_superseded,
                weights=w,
            )
            score = result["total_score"]
            score_sum += score
            total_evaluated += 1

            # 评分分布统计
            bucket = min(4, int(score / 0.2))
            bucket_key = list(score_ranges.keys())[bucket]
            score_ranges[bucket_key] += 1

            # 更新 importance_score
            old_score = float(frag.get("importance_score") or 0.5)
            if abs(score - old_score) > 0.01:
                db.execute(
                    "UPDATE memory_fragments SET importance_score = ? WHERE id = ?",
                    (score, frag_id),
                )
                if score < old_score:
                    total_downweighted += 1

            # 自动遗忘：低于阈值的记忆标记为冷记忆
            if auto_forget and score < forget_threshold:
                try:
                    mark_cold(
                        user_id=frag["user_id"],
                        memory_type="fragment",
                        memory_id=str(frag_id),
                        reason=f"smart_forgetting: score={score:.3f} < threshold={forget_threshold}",
                    )
                    total_forgotten += 1
                except Exception as e:
                    logger.warning(f"标记冷记忆失败 (fragment_id={frag_id}): {e}")

        avg_score = round(score_sum / max(1, total_evaluated), 4)

        logger.info(
            f"✓ 智能遗忘: 评估 {total_evaluated} 条, 降权 {total_downweighted} 条, "
            f"遗忘 {total_forgotten} 条, 平均分 {avg_score}"
        )

        return {
            "success": True,
            "total_evaluated": total_evaluated,
            "total_forgotten": total_forgotten,
            "total_downweighted": total_downweighted,
            "average_score": avg_score,
            "score_distribution": score_ranges,
            "forget_threshold": forget_threshold,
            "weights": w,
        }

    except Exception as e:
        logger.error(f"智能遗忘重算失败: {e}")
        return {
            "success": False,
            "error": str(e),
            "total_evaluated": 0,
            "total_forgotten": 0,
        }


# ============================================================
# 3. 单条记忆重要性查询
# ============================================================

def get_importance_breakdown(
    user_id: int,
    fragment_id: int,
    weights: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """获取单条记忆的重要性评分分解。

    Args:
        user_id: 用户 ID
        fragment_id: 记忆片段 ID
        weights: 自定义权重

    Returns:
        {
            "success": bool,
            "fragment_id": int,
            "content": str,
            "current_score": float,       # 当前存储的 importance_score
            "computed_score": float,      # 重新计算的评分
            "factors": Dict[str, float],  # 各因子分解
            "recall_count": int,
            "is_superseded": bool,
        }
    """
    try:
        db = get_db_client()
        rows = db.execute(
            """SELECT id, user_id, fragment_type, content, importance_score,
                      created_at, lifecycle_status
               FROM memory_fragments WHERE id = ? AND user_id = ?""",
            (fragment_id, user_id),
        )
        if not rows:
            return {"success": False, "error": "记忆片段未找到"}

        frag = dict(rows[0])
        recall_count = _get_recall_count(fragment_id)
        is_superseded = frag.get("lifecycle_status") == "superseded"

        result = compute_importance_score(
            fragment=frag,
            recall_count=recall_count,
            is_superseded=is_superseded,
            weights=weights,
        )

        return {
            "success": True,
            "fragment_id": fragment_id,
            "content": frag.get("content", "")[:200],
            "fragment_type": frag.get("fragment_type"),
            "lifecycle_status": frag.get("lifecycle_status"),
            "current_score": float(frag.get("importance_score") or 0.5),
            "computed_score": result["total_score"],
            "factors": result["factors"],
            "weights": result["weights"],
            "recall_count": recall_count,
            "is_superseded": is_superseded,
        }

    except Exception as e:
        logger.error(f"重要性评分查询失败: {e}")
        return {"success": False, "error": str(e)}


# ============================================================
# 4. 遗忘统计
# ============================================================

def get_forgetting_statistics(
    user_id: Optional[int] = None,
    workspace_id: Optional[int] = None,
) -> Dict[str, Any]:
    """获取智能遗忘统计信息。"""
    try:
        db = get_db_client()

        conditions = []
        params: List[Any] = []
        if user_id is not None:
            conditions.append("user_id = ?")
            params.append(user_id)

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        # 按生命周期状态统计
        status_rows = db.execute(
            f"""SELECT lifecycle_status, COUNT(*) as cnt, AVG(importance_score) as avg_score
               FROM memory_fragments
               {where_clause}
               GROUP BY lifecycle_status""",
            tuple(params) if params else (),
        )

        status_distribution = {}
        for row in (status_rows or []):
            status_distribution[row["lifecycle_status"]] = {
                "count": row["cnt"],
                "avg_importance": round(row["avg_score"] or 0, 4) if row["avg_score"] else 0,
            }

        # 评分分布
        score_rows = db.execute(
            f"""SELECT
                SUM(CASE WHEN importance_score < 0.2 THEN 1 ELSE 0 END) as very_low,
                SUM(CASE WHEN importance_score >= 0.2 AND importance_score < 0.4 THEN 1 ELSE 0 END) as low,
                SUM(CASE WHEN importance_score >= 0.4 AND importance_score < 0.6 THEN 1 ELSE 0 END) as medium,
                SUM(CASE WHEN importance_score >= 0.6 AND importance_score < 0.8 THEN 1 ELSE 0 END) as high,
                SUM(CASE WHEN importance_score >= 0.8 THEN 1 ELSE 0 END) as very_high,
                COUNT(*) as total,
                AVG(importance_score) as avg_score
               FROM memory_fragments
               {where_clause}""",
            tuple(params) if params else (),
        )

        score_dist = {}
        if score_rows and score_rows[0]:
            r = dict(score_rows[0])
            score_dist = {
                "very_low (0.0-0.2)": r.get("very_low", 0),
                "low (0.2-0.4)": r.get("low", 0),
                "medium (0.4-0.6)": r.get("medium", 0),
                "high (0.6-0.8)": r.get("high", 0),
                "very_high (0.8-1.0)": r.get("very_high", 0),
                "total": r.get("total", 0),
                "average_score": round(r.get("avg_score", 0) or 0, 4),
            }

        return {
            "success": True,
            "status_distribution": status_distribution,
            "score_distribution": score_dist,
            "weights": DEFAULT_WEIGHTS,
            "forget_threshold": DEFAULT_FORGET_THRESHOLD,
        }

    except Exception as e:
        logger.error(f"遗忘统计查询失败: {e}")
        return {"success": False, "error": str(e)}


# ============================================================
# 5. 内部辅助函数
# ============================================================

def _get_recall_count(fragment_id: int) -> int:
    """获取指定记忆片段的召回次数。"""
    try:
        db = get_db_client()
        rows = db.execute(
            """SELECT COUNT(*) as cnt FROM memory_trace_events
               WHERE memory_id = ? AND event_type = 'recalled'""",
            (str(fragment_id),),
        )
        return rows[0]["cnt"] if rows else 0
    except Exception:
        return 0


def _batch_get_recall_counts(fragment_ids: List[int]) -> Dict[int, int]:
    """批量获取多个记忆片段的召回次数。"""
    if not fragment_ids:
        return {}
    try:
        db = get_db_client()
        # 分批查询避免 SQL 参数过多
        result: Dict[int, int] = {}
        batch = 100
        for i in range(0, len(fragment_ids), batch):
            chunk = fragment_ids[i:i + batch]
            placeholders = ",".join("?" * len(chunk))
            rows = db.execute(
                f"""SELECT memory_id, COUNT(*) as cnt FROM memory_trace_events
                   WHERE memory_id IN ({placeholders}) AND event_type = 'recalled'
                   GROUP BY memory_id""",
                tuple(str(fid) for fid in chunk),
            )
            for row in (rows or []):
                try:
                    result[int(row["memory_id"])] = row["cnt"]
                except (ValueError, TypeError):
                    pass
        return result
    except Exception:
        return {}


def _get_superseded_ids(user_id: Optional[int] = None) -> set:
    """获取所有被标记为 superseded 的记忆片段 ID 集合。"""
    try:
        db = get_db_client()
        if user_id is not None:
            rows = db.execute(
                "SELECT id FROM memory_fragments WHERE lifecycle_status = 'superseded' AND user_id = ?",
                (user_id,),
            )
        else:
            rows = db.execute(
                "SELECT id FROM memory_fragments WHERE lifecycle_status = 'superseded'"
            )
        return {row["id"] for row in (rows or [])}
    except Exception:
        return set()
