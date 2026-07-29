"""
冲突自动解决引擎（W2-F2.3）

在矛盾检测（contradiction_service）基础上，增加策略化的自动解决能力：

| 策略 | 规则 | 适用场景 |
|------|------|---------|
| latest_wins       | 新记忆自动取代旧记忆 | 默认策略 |
| confidence_based  | 比较 importance × evidence 因子，高分者胜 | 保护高可信度旧记忆 |
| manual_review     | 标记 conflict_pending，等待用户确认 | 双高重要性冲突 |

策略选择规则：
- old_importance ≥ 0.9 且 new_importance ≥ 0.9 → manual_review（双高，无法自动裁决）
- old_importance ≥ 0.9 且 new_importance < 0.7 → confidence_based（保护高价值旧记忆）
- 其他 → latest_wins（默认，身份/偏好以最新为准）
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.core.db_client import get_db_client

logger = logging.getLogger(__name__)

_SUPERSEDED_STATUS = "superseded"
_CONFLICT_PENDING = "conflict_pending"

STRATEGY_LATEST_WINS = "latest_wins"
STRATEGY_CONFIDENCE = "confidence_based"
STRATEGY_MANUAL = "manual_review"


# ============================================================
# 数据类
# ============================================================

@dataclass
class ResolutionResult:
    """冲突解决结果"""
    strategy: str = STRATEGY_LATEST_WINS
    action: str = ""             # superseded_old | kept_old | pending_review
    winner_id: Optional[int] = None
    loser_id: Optional[int] = None
    conflict_id: Optional[int] = None  # manual_review 时的待处理记录 ID
    detail: Dict[str, Any] = field(default_factory=dict)


# ============================================================
# memory_conflicts 表
# ============================================================

def _ensure_conflicts_table() -> None:
    """确保 memory_conflicts 表存在（migrations v8 兜底）。"""
    db = get_db_client()
    try:
        db.execute('''CREATE TABLE IF NOT EXISTS memory_conflicts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            workspace_id INTEGER,
            old_fragment_id INTEGER NOT NULL,
            new_fragment_id INTEGER,
            entity_type TEXT,
            detection_method TEXT DEFAULT 'pattern',
            suggested_strategy TEXT DEFAULT 'latest_wins',
            status TEXT DEFAULT 'conflict_pending',
            resolution TEXT,
            resolution_reason TEXT,
            resolved_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )''')
        db.execute(
            'CREATE INDEX IF NOT EXISTS idx_conflicts_user_status '
            'ON memory_conflicts(user_id, status)'
        )
    except Exception as e:
        logger.error(f"创建 memory_conflicts 表失败: {e}")


# ============================================================
# ConflictResolver
# ============================================================

class ConflictResolver:
    """冲突自动解决器"""

    def resolve(
        self,
        old_fragment: Dict[str, Any],
        new_fragment: Dict[str, Any],
        contradiction_info: Optional[Dict[str, Any]] = None,
    ) -> ResolutionResult:
        """根据策略解决一对记忆冲突。

        Args:
            old_fragment: 旧记忆 dict（需含 id, importance_score）
            new_fragment: 新记忆 dict（需含 id, importance_score；id 可为 None）
            contradiction_info: 矛盾检测信息（entity_type, detection_method 等）

        Returns:
            ResolutionResult
        """
        info = contradiction_info or {}
        strategy = self._select_strategy(old_fragment, new_fragment)

        if strategy == STRATEGY_LATEST_WINS:
            return self._apply_latest_wins(old_fragment, new_fragment)
        elif strategy == STRATEGY_CONFIDENCE:
            return self._apply_confidence_based(old_fragment, new_fragment)
        else:
            return self._mark_for_review(old_fragment, new_fragment, info)

    # ----------------------------------------------------------
    # 策略选择
    # ----------------------------------------------------------

    def _select_strategy(
        self,
        old_fragment: Dict[str, Any],
        new_fragment: Dict[str, Any],
    ) -> str:
        old_imp = _to_float(old_fragment.get("importance_score"), 0.5)
        new_imp = _to_float(new_fragment.get("importance_score"), 0.5)

        # 双高重要性 → 人工审核
        if old_imp >= 0.9 and new_imp >= 0.9:
            return STRATEGY_MANUAL
        # 高价值旧记忆 vs 低可信度新记忆 → 可信度比较
        if old_imp >= 0.9 and new_imp < 0.7:
            return STRATEGY_CONFIDENCE
        # 默认：最新为准
        return STRATEGY_LATEST_WINS

    # ----------------------------------------------------------
    # 策略实现
    # ----------------------------------------------------------

    def _apply_latest_wins(
        self,
        old_fragment: Dict[str, Any],
        new_fragment: Dict[str, Any],
    ) -> ResolutionResult:
        """新记忆取代旧记忆：旧记忆标记 superseded。"""
        old_id = old_fragment.get("id")
        user_id = old_fragment.get("user_id")
        if old_id and user_id:
            _mark_fragment_status(user_id, old_id, _SUPERSEDED_STATUS)
        return ResolutionResult(
            strategy=STRATEGY_LATEST_WINS,
            action="superseded_old",
            winner_id=new_fragment.get("id"),
            loser_id=old_id,
        )

    def _apply_confidence_based(
        self,
        old_fragment: Dict[str, Any],
        new_fragment: Dict[str, Any],
    ) -> ResolutionResult:
        """可信度比较：importance × evidence 因子，高分者胜。

        evidence 因子：被召回过的记忆有实证支撑，+0.1；
        近 7 天创建的新鲜记忆 +0.05。
        """
        old_score = self._confidence_score(old_fragment)
        new_score = self._confidence_score(new_fragment)

        if old_score >= new_score:
            # 旧记忆胜出：保持 active，新记忆标记 superseded（低可信不覆盖）
            new_id = new_fragment.get("id")
            user_id = new_fragment.get("user_id") or old_fragment.get("user_id")
            if new_id and user_id:
                _mark_fragment_status(user_id, new_id, _SUPERSEDED_STATUS)
            return ResolutionResult(
                strategy=STRATEGY_CONFIDENCE,
                action="kept_old",
                winner_id=old_fragment.get("id"),
                loser_id=new_id,
                detail={"old_score": round(old_score, 4), "new_score": round(new_score, 4)},
            )
        else:
            # 新记忆胜出：旧记忆标记 superseded
            old_id = old_fragment.get("id")
            user_id = old_fragment.get("user_id")
            if old_id and user_id:
                _mark_fragment_status(user_id, old_id, _SUPERSEDED_STATUS)
            return ResolutionResult(
                strategy=STRATEGY_CONFIDENCE,
                action="superseded_old",
                winner_id=new_fragment.get("id"),
                loser_id=old_id,
                detail={"old_score": round(old_score, 4), "new_score": round(new_score, 4)},
            )

    def _mark_for_review(
        self,
        old_fragment: Dict[str, Any],
        new_fragment: Dict[str, Any],
        info: Dict[str, Any],
    ) -> ResolutionResult:
        """标记为 conflict_pending，等待用户确认（双方均不变更）。"""
        _ensure_conflicts_table()
        db = get_db_client()
        user_id = old_fragment.get("user_id") or new_fragment.get("user_id")
        conflict_id = db.execute(
            """INSERT INTO memory_conflicts
               (user_id, workspace_id, old_fragment_id, new_fragment_id,
                entity_type, detection_method, suggested_strategy, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                user_id,
                old_fragment.get("workspace_id"),
                old_fragment.get("id"),
                new_fragment.get("id"),
                info.get("entity_type"),
                info.get("detection_method", "pattern"),
                STRATEGY_CONFIDENCE,
                _CONFLICT_PENDING,
            ),
        )
        logger.info(
            f"⚠ 冲突待审核: conflict_id={conflict_id}, "
            f"old={old_fragment.get('id')}, new={new_fragment.get('id')}"
        )
        return ResolutionResult(
            strategy=STRATEGY_MANUAL,
            action="pending_review",
            conflict_id=conflict_id,
        )

    # ----------------------------------------------------------
    # 内部辅助
    # ----------------------------------------------------------

    def _confidence_score(self, fragment: Dict[str, Any]) -> float:
        """importance × evidence 因子。"""
        importance = _to_float(fragment.get("importance_score"), 0.5)
        evidence = 1.0
        # 被召回过 → 有实证支撑
        if fragment.get("last_recalled_at"):
            evidence += 0.1
        # 近 7 天创建 → 新鲜度加成
        created_at = fragment.get("created_at")
        if created_at:
            try:
                created = datetime.fromisoformat(str(created_at).replace("Z", "+00:00").split("+")[0])
                if (datetime.now() - created).days <= 7:
                    evidence += 0.05
            except (ValueError, TypeError):
                pass
        return importance * evidence


# ============================================================
# 冲突列表与手动解决
# ============================================================

def list_conflicts(
    user_id: int,
    status: str = _CONFLICT_PENDING,
    limit: int = 50,
) -> Dict[str, Any]:
    """列出用户的冲突记录（默认仅 pending）。

    Returns:
        {"success": bool, "conflicts": List[Dict], "total": int}
    """
    try:
        _ensure_conflicts_table()
        db = get_db_client()
        rows = db.execute(
            """SELECT * FROM memory_conflicts
               WHERE user_id = ? AND status = ?
               ORDER BY created_at DESC LIMIT ?""",
            (user_id, status, limit),
        )
        conflicts = []
        for row in (rows or []):
            entry = dict(row)
            # 附加片段详情
            for key, frag_key in (("old_fragment_id", "old_fragment"), ("new_fragment_id", "new_fragment")):
                fid = entry.get(key)
                if fid:
                    frag_rows = db.execute(
                        "SELECT id, content, importance_score, lifecycle_status FROM memory_fragments WHERE id = ?",
                        (fid,),
                    )
                    entry[frag_key] = dict(frag_rows[0]) if frag_rows else None
            conflicts.append(entry)
        return {"success": True, "conflicts": conflicts, "total": len(conflicts)}
    except Exception as e:
        logger.error(f"冲突列表查询失败: {e}")
        return {"success": False, "error": str(e), "conflicts": [], "total": 0}


def resolve_conflict_manual(
    user_id: int,
    conflict_id: int,
    resolution: str,
    reason: str = "",
) -> Dict[str, Any]:
    """用户手动解决冲突。

    Args:
        user_id: 用户 ID
        conflict_id: 冲突记录 ID
        resolution: keep_old | keep_new | keep_both
        reason: 解决理由

    Returns:
        {"success": bool, "action_taken": str, "affected_fragments": List[int]}
    """
    if resolution not in ("keep_old", "keep_new", "keep_both"):
        return {"success": False, "error": f"无效的 resolution: {resolution}"}

    try:
        _ensure_conflicts_table()
        db = get_db_client()
        rows = db.execute(
            "SELECT * FROM memory_conflicts WHERE id = ? AND user_id = ?",
            (conflict_id, user_id),
        )
        if not rows:
            return {"success": False, "error": f"冲突记录不存在: {conflict_id}"}

        conflict = dict(rows[0])
        if conflict["status"] != _CONFLICT_PENDING:
            return {"success": False, "error": f"冲突已解决: status={conflict['status']}"}

        affected: List[int] = []
        old_id = conflict.get("old_fragment_id")
        new_id = conflict.get("new_fragment_id")

        if resolution == "keep_old" and new_id:
            _mark_fragment_status(user_id, new_id, _SUPERSEDED_STATUS)
            affected.append(new_id)
        elif resolution == "keep_new" and old_id:
            _mark_fragment_status(user_id, old_id, _SUPERSEDED_STATUS)
            affected.append(old_id)
        # keep_both: 双方均保持 active，不做变更

        db.execute(
            """UPDATE memory_conflicts
               SET status = 'resolved', resolution = ?, resolution_reason = ?, resolved_at = ?
               WHERE id = ?""",
            (resolution, reason, datetime.now().isoformat(), conflict_id),
        )

        logger.info(f"✓ 冲突手动解决: conflict_id={conflict_id}, resolution={resolution}")
        return {
            "success": True,
            "resolved_conflict_id": conflict_id,
            "action_taken": resolution,
            "affected_fragments": affected,
        }
    except Exception as e:
        logger.error(f"冲突手动解决失败: {e}")
        return {"success": False, "error": str(e)}


# ============================================================
# 模块级辅助
# ============================================================

def _mark_fragment_status(user_id: int, fragment_id: int, status: str) -> None:
    """更新记忆片段生命周期状态。"""
    db = get_db_client()
    db.execute(
        "UPDATE memory_fragments SET lifecycle_status = ? WHERE id = ? AND user_id = ?",
        (status, fragment_id, user_id),
    )


def _to_float(value: Any, default: float = 0.5) -> float:
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


# 全局单例
_resolver = ConflictResolver()


def get_conflict_resolver() -> ConflictResolver:
    """获取全局 ConflictResolver 实例。"""
    return _resolver
