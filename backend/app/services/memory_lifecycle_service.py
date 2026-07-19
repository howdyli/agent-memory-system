"""
Memory Lifecycle - 智能记忆生命周期管理模块

核心功能：
1. 差异化半衰期机制：基于记忆类型的不同过期策略
2. 弹性驱逐策略：冷记忆标记、归档、软删除、恢复
3. 自动合并与冲突检测：重复检测、冲突告警、合并审计
"""
import logging
import json
import math
import hashlib
import asyncio
from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

from app.core.db_client import get_db_client
from app.core.tracing import get_tracer
from app.services.memory_observability_service import record_trace_event
from app.services.memory_fragment_service import (
    get_fragment,
    update_fragment,
    list_fragments,
)
from app.services.memory_variable_service import (
    list_memory_variables,
    get_memory_variable,
    set_memory_variable,
)
from app.services.long_term_memory_service import record_version

# ============================================================
# 半衰期配置
# ============================================================

# 基于 fragment_type 的差异化半衰期（天）
HALF_LIFE_CONFIG = {
    # 用户基本信息（永久存储）
    "info": {
        "half_life_days": None,       # None = 永久
        "description": "用户基本信息（姓名、职业、联系方式等）",
        "decay_enabled": False,
    },
    # 计划/项目信息（90 天半衰期）
    "plan": {
        "half_life_days": 90,
        "description": "项目信息、工作安排、待办事项",
        "decay_enabled": True,
    },
    # 临时偏好（1 天半衰期）
    "preference": {
        "half_life_days": 1,
        "description": "临时偏好、短期兴趣、临时计划",
        "decay_enabled": True,
    },
}

# 默认半衰期
DEFAULT_HALF_LIFE_DAYS = 30
DEFAULT_COLD_THRESHOLD = 0.3
DEFAULT_COLD_UNRECALLED_DAYS = 30


# ============================================================
# 工具函数
# ============================================================

def _ensure_lifecycle_tables():
    """确保生命周期相关表存在（初次使用时调用）"""
    from app.core.db_client import get_db_client
    db = get_db_client()
    # 这些表在 db_client.py 的 _ensure_database_exists 中创建，
    # 但为了独立使用也做一次保障
    for sql in [
        '''CREATE TABLE IF NOT EXISTS memory_lifecycle (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            memory_type TEXT NOT NULL,
            memory_id TEXT NOT NULL,
            lifecycle_status TEXT DEFAULT 'active',
            cold_reason TEXT, cold_at TIMESTAMP,
            last_recalled_at TIMESTAMP, archived_at TIMESTAMP,
            soft_deleted_at TIMESTAMP, restore_count INTEGER DEFAULT 0,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )''',
        '''CREATE TABLE IF NOT EXISTS memory_delete_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL, memory_type TEXT NOT NULL,
            memory_id TEXT NOT NULL, action TEXT NOT NULL,
            reason TEXT, old_content TEXT,
            operator TEXT DEFAULT 'user',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )''',
        '''CREATE TABLE IF NOT EXISTS memory_merge_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL, memory_type TEXT NOT NULL,
            source_ids TEXT NOT NULL, target_id TEXT,
            merge_type TEXT NOT NULL, merge_action TEXT NOT NULL,
            similarity_score REAL, old_value TEXT, new_value TEXT,
            conflict_type TEXT, operator TEXT DEFAULT 'system',
            resolved INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            resolved_at TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )''',
    ]:
        try:
            db.execute(sql)
        except Exception:
            pass

    # 兼容旧表结构：动态补充冲突类型与操作者字段
    # 先检查列是否已存在，避免重复 ALTER TABLE 产生错误日志
    try:
        columns = db.execute("PRAGMA table_info(memory_merge_log)")
        existing_cols = {row["name"] for row in columns} if columns else set()
        if "conflict_type" not in existing_cols:
            db.execute("ALTER TABLE memory_merge_log ADD COLUMN conflict_type TEXT")
        if "operator" not in existing_cols:
            db.execute("ALTER TABLE memory_merge_log ADD COLUMN operator TEXT DEFAULT 'system'")
    except Exception:
        pass


# ============================================================
# 1. 差异化半衰期机制
# ============================================================

def get_half_life(fragment_type: str) -> Optional[int]:
    """
    获取指定记忆类型的半衰期（天）。

    Args:
        fragment_type: 记忆片段类型（info, preference, plan）

    Returns:
        半衰期天数，None 表示永久存储
    """
    cfg = HALF_LIFE_CONFIG.get(fragment_type)
    if cfg:
        return cfg["half_life_days"]
    return DEFAULT_HALF_LIFE_DAYS


def get_half_life_info(fragment_type: str) -> Dict[str, Any]:
    """
    获取半衰期配置的详细信息（供 API 返回）。

    Args:
        fragment_type: 记忆片段类型

    Returns:
        配置信息字典
    """
    cfg = HALF_LIFE_CONFIG.get(fragment_type, {
        "half_life_days": DEFAULT_HALF_LIFE_DAYS,
        "description": "默认类型",
        "decay_enabled": True,
    })
    return {
        "fragment_type": fragment_type,
        "half_life_days": cfg["half_life_days"],
        "description": cfg["description"],
        "decay_enabled": cfg["decay_enabled"],
    }


def calculate_decay_score(
    created_at: Any,
    half_life_days: Optional[int],
) -> float:
    """
    基于半衰期计算记忆的衰减分数。

    使用指数衰减公式: score = 2^(-days_since / half_life_days)
    - half_life_days = None 时返回 1.0（不衰减）

    Args:
        created_at: 创建时间（datetime 或 ISO 字符串）
        half_life_days: 半衰期天数，None 表示永久

    Returns:
        衰减分数（0.0 ~ 1.0）
    """
    if half_life_days is None:
        return 1.0

    if created_at is None:
        return 1.0

    try:
        if isinstance(created_at, str):
            created_time = datetime.fromisoformat(
                created_at.replace("Z", "+00:00").split(".")[0]
            )
        else:
            created_time = created_at

        days_since = max(0, (datetime.now() - created_time).days)
        decay = math.pow(2, -days_since / half_life_days)
        return max(0.0, min(1.0, decay))
    except Exception:
        return 1.0


def estimate_remaining_life(fragment: Dict[str, Any]) -> Dict[str, Any]:
    """
    预估记忆片段的剩余存活时间。

    Args:
        fragment: 记忆片段字典（需包含 fragment_type, created_at, importance_score）

    Returns:
        预估结果:
        {
            "half_life_days": ...,
            "elapsed_days": ...,
            "remaining_days": ...,
            "decay_score": ...,
            "is_permanent": True/False
        }
    """
    fragment_type = fragment.get("fragment_type", "unknown")
    half_life_days = get_half_life(fragment_type)

    if half_life_days is None:
        return {
            "half_life_days": None,
            "elapsed_days": None,
            "remaining_days": None,
            "decay_score": 1.0,
            "is_permanent": True,
        }

    created_at = fragment.get("created_at")
    elapsed_days = None
    if created_at:
        try:
            if isinstance(created_at, str):
                created_time = datetime.fromisoformat(
                    created_at.replace("Z", "+00:00").split(".")[0]
                )
            else:
                created_time = created_at
            elapsed_days = max(0, (datetime.now() - created_time).days)
        except Exception:
            pass

    decay_score = calculate_decay_score(created_at, half_life_days)

    # 预估剩余天数：当衰减分数 < 0.05 时视为"接近过期"
    remaining_days = None
    if elapsed_days is not None and half_life_days:
        # 5% 衰减阈值对应的天数: half_life * log2(1/0.05) ≈ half_life * 4.32
        effective_life = int(half_life_days * 4.32)
        remaining_days = max(0, effective_life - elapsed_days)

    return {
        "half_life_days": half_life_days,
        "elapsed_days": elapsed_days,
        "remaining_days": remaining_days,
        "decay_score": round(decay_score, 4),
        "is_permanent": False,
    }


def cleanup_expired_memories(workspace_id: Optional[int] = None) -> Dict[str, Any]:
    """
    批量清理已过期的记忆。

    基于 memory_fragments.expires_at 字段清理过期片段，
    同时在 memory_lifecycle 中标记。

    Returns:
        清理结果统计
    """
    try:
        _ensure_lifecycle_tables()
        db = get_db_client()
        now = datetime.now().isoformat()

        # 1. 查找已过期的片段
        expired = db.execute(
            '''SELECT id, user_id, fragment_type, content
               FROM memory_fragments
               WHERE expires_at IS NOT NULL AND expires_at < ?
               AND lifecycle_status = 'active' ''',
            (now,)
        )

        expired_list = [dict(r) for r in expired] if expired else []
        expired_count = len(expired_list)

        if expired_count == 0:
            return {"success": True, "cleaned": 0, "message": "无过期记忆"}

        # 2. 标记过期片段为 archived 而非直接删除（保留审计）
        for row in expired_list:
            # 更新 lifecycle 记录
            _upsert_lifecycle(
                user_id=row["user_id"],
                memory_type="fragment",
                memory_id=str(row["id"]),
                lifecycle_status="archived",
                archived_at=now,
                cold_reason="TTL expired",
            )
            # 更新 fragment 状态
            db.execute(
                '''UPDATE memory_fragments
                   SET lifecycle_status = 'archived'
                   WHERE id = ?''',
                (row["id"],)
            )

        logger.info(f"✓ 过期记忆清理: {expired_count} 条已归档")
        return {
            "success": True,
            "cleaned": expired_count,
            "message": f"已归档 {expired_count} 条过期记忆",
        }

    except Exception as e:
        logger.error(f"✗ 过期记忆清理失败: {e}")
        return {"success": False, "error": str(e)}


# ============================================================
# 2. 弹性驱逐策略
# ============================================================

def _upsert_lifecycle(
    user_id: int,
    memory_type: str,
    memory_id: str,
    lifecycle_status: str = "active",
    cold_reason: Optional[str] = None,
    cold_at: Optional[str] = None,
    last_recalled_at: Optional[str] = None,
    archived_at: Optional[str] = None,
    soft_deleted_at: Optional[str] = None,
    workspace_id: Optional[int] = None,
) -> bool:
    """创建或更新 memory_lifecycle 记录"""
    try:
        _ensure_lifecycle_tables()
        db = get_db_client()

        rows = db.execute(
            '''SELECT id FROM memory_lifecycle
               WHERE user_id = ? AND memory_type = ? AND memory_id = ?''',
            (user_id, memory_type, memory_id)
        )

        now = datetime.now().isoformat()

        if rows:
            existing_id = rows[0]["id"]
            updates = []
            params = []
            updates.append('lifecycle_status = ?')
            params.append(lifecycle_status)
            if cold_reason is not None:
                updates.append('cold_reason = ?')
                params.append(cold_reason)
            if cold_at is not None:
                updates.append('cold_at = ?')
                params.append(cold_at)
            if last_recalled_at is not None:
                updates.append('last_recalled_at = ?')
                params.append(last_recalled_at)
            if archived_at is not None:
                updates.append('archived_at = ?')
                params.append(archived_at)
            if soft_deleted_at is not None:
                updates.append('soft_deleted_at = ?')
                params.append(soft_deleted_at)

            if updates:
                params.append(existing_id)
                db.execute(
                    f'UPDATE memory_lifecycle SET {", ".join(updates)} WHERE id = ?',
                    tuple(params)
                )
        else:
            db.execute(
                '''INSERT INTO memory_lifecycle
                   (user_id, memory_type, memory_id, lifecycle_status,
                    cold_reason, cold_at, last_recalled_at,
                    archived_at, soft_deleted_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                (user_id, memory_type, memory_id, lifecycle_status,
                 cold_reason, cold_at, last_recalled_at,
                 archived_at, soft_deleted_at)
            )
        return True
    except Exception as e:
        logger.error(f"✗ 生命周期更新失败: {e}")
        return False


def _record_delete_log(
    user_id: int,
    memory_type: str,
    memory_id: str,
    action: str,
    reason: Optional[str] = None,
    old_content: Optional[str] = None,
    operator: str = "user",
    workspace_id: Optional[int] = None,
) -> bool:
    """记录删除操作审计日志"""
    try:
        _ensure_lifecycle_tables()
        db = get_db_client()
        db.execute(
            '''INSERT INTO memory_delete_log
               (user_id, memory_type, memory_id, action, reason, old_content, operator)
               VALUES (?, ?, ?, ?, ?, ?, ?)''',
            (user_id, memory_type, memory_id, action, reason, old_content, operator)
        )
        return True
    except Exception as e:
        logger.error(f"✗ 删除日志记录失败: {e}")
        return False


def mark_cold(
    user_id: int,
    memory_type: str,
    memory_id: str,
    reason: str = "importance_below_threshold",
    workspace_id: Optional[int] = None,
) -> Dict[str, Any]:
    """
    将记忆标记为"冷记忆"状态。

    冷记忆不会被自动注入对话上下文，但仍可手动检索。

    Args:
        user_id: 用户 ID
        memory_type: 记忆类型（fragment, variable）
        memory_id: 记忆 ID
        reason: 标记原因

    Returns:
        操作结果
    """
    try:
        now = datetime.now().isoformat()
        ok = _upsert_lifecycle(
            user_id=user_id,
            memory_type=memory_type,
            memory_id=memory_id,
            lifecycle_status="cold",
            cold_reason=reason,
            cold_at=now,
        )

        if ok:
            # 也更新 memory_fragments 表中的 lifecycle_status
            if memory_type == "fragment":
                db = get_db_client()
                db.execute(
                    '''UPDATE memory_fragments SET lifecycle_status = 'cold', cold_at = ?
                       WHERE id = ?''',
                    (now, int(memory_id))
                )

        logger.info(f"✓ 标记冷记忆: {memory_type}:{memory_id} ({reason})")
        try:
            record_trace_event(user_id, memory_id, memory_type, "cold_marked", "lifecycle",
                              metadata={"reason": reason})
        except Exception:
            pass
        return {"success": True, "message": f"Memory {memory_id} marked as cold"}

    except Exception as e:
        logger.error(f"✗ 标记冷记忆失败: {e}")
        return {"success": False, "error": str(e)}


def auto_archive_cold_memories(
    user_id: Optional[int] = None,
    cold_days: int = DEFAULT_COLD_UNRECALLED_DAYS,
    workspace_id: Optional[int] = None,
) -> Dict[str, Any]:
    """
    自动归档长期未被召回的冷记忆。

    Args:
        user_id: 指定用户（None = 所有用户）
        cold_days: 未召回天数阈值

    Returns:
        归档结果统计
    """
    try:
        _ensure_lifecycle_tables()
        db = get_db_client()
        now = datetime.now().isoformat()
        cutoff = (datetime.now() - timedelta(days=cold_days)).isoformat()

        if user_id:
            rows = db.execute(
                '''SELECT lc.id, lc.user_id, lc.memory_type, lc.memory_id
                   FROM memory_lifecycle lc
                   WHERE lc.user_id = ? AND lc.lifecycle_status = 'cold'
                   AND (lc.last_recalled_at IS NULL OR lc.last_recalled_at < ?)''',
                (user_id, cutoff)
            )
        else:
            rows = db.execute(
                '''SELECT lc.id, lc.user_id, lc.memory_type, lc.memory_id
                   FROM memory_lifecycle lc
                   WHERE lc.lifecycle_status = 'cold'
                   AND (lc.last_recalled_at IS NULL OR lc.last_recalled_at < ?)''',
                (cutoff,)
            )

        candidates = [dict(r) for r in rows] if rows else []
        archived_count = 0

        for row in candidates:
            try:
                # 更新 lifecycle 表
                db.execute(
                    '''UPDATE memory_lifecycle SET lifecycle_status = 'archived',
                       archived_at = ? WHERE id = ?''',
                    (now, row["id"])
                )
                # 更新 fragment 表
                if row["memory_type"] == "fragment":
                    db.execute(
                        '''UPDATE memory_fragments SET lifecycle_status = 'archived'
                           WHERE id = ?''',
                        (int(row["memory_id"]),)
                    )
                archived_count += 1
            except Exception:
                continue

        logger.info(f"✓ 自动归档冷记忆: {archived_count} 条")
        return {
            "success": True,
            "archived": archived_count,
            "message": f"已归档 {archived_count} 条冷记忆",
        }

    except Exception as e:
        logger.error(f"✗ 自动归档失败: {e}")
        return {"success": False, "error": str(e)}


def soft_delete(
    user_id: int,
    memory_type: str,
    memory_id: str,
    reason: Optional[str] = None,
    workspace_id: Optional[int] = None,
) -> Dict[str, Any]:
    """
    软删除记忆（标记删除状态，可恢复）。

    操作步骤：
    1. 备份原始内容到 delete_log
    2. 标记 lifecycle_status = 'soft_deleted'
    3. 记录版本和审计日志

    Args:
        user_id: 用户 ID
        memory_type: 记忆类型
        memory_id: 记忆 ID
        reason: 删除原因

    Returns:
        操作结果
    """
    try:
        _ensure_lifecycle_tables()
        db = get_db_client()
        now = datetime.now().isoformat()

        # 1. 获取原始内容作为备份
        old_content = None
        if memory_type == "fragment":
            frag_result = get_fragment(user_id, int(memory_id))
            if frag_result.get("success"):
                old_content = json.dumps(
                    frag_result["fragment"], ensure_ascii=False
                )
        elif memory_type == "variable":
            value = get_memory_variable(user_id, memory_id)
            if value is not None:
                old_content = json.dumps(value, ensure_ascii=False)

        # 2. 记录删除审计日志
        _record_delete_log(
            user_id=user_id,
            memory_type=memory_type,
            memory_id=memory_id,
            action="soft_delete",
            reason=reason,
            old_content=old_content,
        )

        # 3. 更新 lifecycle 状态
        _upsert_lifecycle(
            user_id=user_id,
            memory_type=memory_type,
            memory_id=memory_id,
            lifecycle_status="soft_deleted",
            soft_deleted_at=now,
        )

        # 4. 更新 fragment/variable 状态
        if memory_type == "fragment":
            db.execute(
                '''UPDATE memory_fragments SET lifecycle_status = 'soft_deleted'
                   WHERE id = ?''',
                (int(memory_id),)
            )

        # 5. 记录版本
        try:
            record_version(
                user_id=user_id,
                memory_type=memory_type,
                memory_id=memory_id,
                action="soft_delete",
                old_value=old_content,
                new_value=None,
            )
        except Exception:
            pass

        logger.info(f"✓ 软删除: {memory_type}:{memory_id}")
        try:
            record_trace_event(user_id, memory_id, memory_type, "deleted", "lifecycle",
                              metadata={"reason": f"soft_delete"})
        except Exception:
            pass
        return {
            "success": True,
            "message": f"Memory {memory_id} soft deleted",
            "old_content_saved": bool(old_content),
        }

    except Exception as e:
        logger.error(f"✗ 软删除失败: {e}")
        return {"success": False, "error": str(e)}


def restore_memory(
    user_id: int,
    memory_type: str,
    memory_id: str,
    workspace_id: Optional[int] = None,
) -> Dict[str, Any]:
    """
    恢复软删除的记忆。

    Args:
        user_id: 用户 ID
        memory_type: 记忆类型
        memory_id: 记忆 ID

    Returns:
        操作结果
    """
    try:
        _ensure_lifecycle_tables()
        db = get_db_client()
        now = datetime.now().isoformat()

        # 1. 获取当前 lifecycle 记录
        rows = db.execute(
            '''SELECT * FROM memory_lifecycle
               WHERE user_id = ? AND memory_type = ? AND memory_id = ?''',
            (user_id, memory_type, memory_id)
        )
        if not rows:
            return {"success": False, "error": "记忆未找到"}

        lc = dict(rows[0])
        if lc["lifecycle_status"] != "soft_deleted":
            return {"success": False, "error": f"记忆状态为 {lc['lifecycle_status']}，无法恢复"}

        # 2. 恢复状态
        restore_count = (lc.get("restore_count") or 0) + 1
        db.execute(
            '''UPDATE memory_lifecycle SET lifecycle_status = 'active',
               soft_deleted_at = NULL, restore_count = ?
               WHERE id = ?''',
            (restore_count, lc["id"])
        )

        if memory_type == "fragment":
            db.execute(
                '''UPDATE memory_fragments SET lifecycle_status = 'active'
                   WHERE id = ?''',
                (int(memory_id),)
            )

        # 3. 记录恢复日志
        _record_delete_log(
            user_id=user_id,
            memory_type=memory_type,
            memory_id=memory_id,
            action="restore",
            reason=f"User restored (restore_count={restore_count})",
        )

        logger.info(f"✓ 恢复记忆: {memory_type}:{memory_id}")
        try:
            record_trace_event(user_id, memory_id, memory_type, "restored", "lifecycle",
                              metadata={"restore_count": restore_count})
        except Exception:
            pass
        return {"success": True, "message": f"Memory {memory_id} restored"}

    except Exception as e:
        logger.error(f"✗ 恢复记忆失败: {e}")
        return {"success": False, "error": str(e)}


def hard_delete(
    user_id: int,
    memory_type: str,
    memory_id: str,
    reason: Optional[str] = None,
    workspace_id: Optional[int] = None,
) -> Dict[str, Any]:
    """
    永久删除记忆（不可恢复）。

    操作步骤：
    1. 记录完整审计日志（含备份）
    2. 从 memory_fragments 和生命周期表中永久删除

    Args:
        user_id: 用户 ID
        memory_type: 记忆类型
        memory_id: 记忆 ID
        reason: 删除原因

    Returns:
        操作结果
    """
    try:
        _ensure_lifecycle_tables()
        db = get_db_client()

        # 1. 备份原始内容
        old_content = None
        if memory_type == "fragment":
            frag_result = get_fragment(user_id, int(memory_id))
            if frag_result.get("success"):
                old_content = json.dumps(
                    frag_result["fragment"], ensure_ascii=False
                )

        # 2. 记录审计日志
        _record_delete_log(
            user_id=user_id,
            memory_type=memory_type,
            memory_id=memory_id,
            action="hard_delete",
            reason=reason,
            old_content=old_content,
        )

        # 3. 记录版本
        try:
            record_version(
                user_id=user_id,
                memory_type=memory_type,
                memory_id=memory_id,
                action="hard_delete",
                old_value=old_content,
                new_value=None,
            )
        except Exception:
            pass

        # 4. 从 lifecycle 表删除
        db.execute(
            '''DELETE FROM memory_lifecycle
               WHERE user_id = ? AND memory_type = ? AND memory_id = ?''',
            (user_id, memory_type, memory_id)
        )

        # 5. 从 fragment/variable 表删除
        if memory_type == "fragment":
            db.execute(
                'DELETE FROM memory_fragments WHERE id = ?',
                (int(memory_id),)
            )

        logger.info(f"✓ 硬删除: {memory_type}:{memory_id}")
        return {"success": True, "message": f"Memory {memory_id} permanently deleted"}

    except Exception as e:
        logger.error(f"✗ 硬删除失败: {e}")
        return {"success": False, "error": str(e)}


def list_cold_memories(
    user_id: int,
    limit: int = 50,
    offset: int = 0,
    workspace_id: Optional[int] = None,
) -> Dict[str, Any]:
    """
    列出用户的冷记忆。

    Args:
        user_id: 用户 ID
        limit: 返回条数
        offset: 偏移量

    Returns:
        冷记忆列表
    """
    try:
        _ensure_lifecycle_tables()
        db = get_db_client()

        rows = db.execute(
            '''SELECT lc.*, f.content, f.fragment_type, f.importance_score,
                      f.created_at
               FROM memory_lifecycle lc
               LEFT JOIN memory_fragments f ON lc.memory_id = CAST(f.id AS TEXT)
               WHERE lc.user_id = ? AND lc.lifecycle_status = 'cold'
               ORDER BY lc.cold_at DESC
               LIMIT ? OFFSET ?''',
            (user_id, limit, offset)
        )

        memories = [dict(r) for r in rows] if rows else []

        count_rows = db.execute(
            '''SELECT COUNT(*) as total FROM memory_lifecycle
               WHERE user_id = ? AND lifecycle_status = 'cold' ''',
            (user_id,)
        )
        total = count_rows[0]["total"] if count_rows else 0

        return {
            "success": True,
            "memories": memories,
            "count": len(memories),
            "total": total,
        }

    except Exception as e:
        logger.error(f"✗ 列出冷记忆失败: {e}")
        return {"success": False, "error": str(e)}


def list_deleted_memories(
    user_id: int,
    limit: int = 50,
    offset: int = 0,
    workspace_id: Optional[int] = None,
) -> Dict[str, Any]:
    """
    列出用户已软删除的记忆（可恢复窗口）。

    Args:
        user_id: 用户 ID
        limit: 返回条数
        offset: 偏移量

    Returns:
        已删除记忆列表
    """
    try:
        _ensure_lifecycle_tables()
        db = get_db_client()

        rows = db.execute(
            '''SELECT lc.*, f.content, f.fragment_type, f.importance_score,
                      f.created_at
               FROM memory_lifecycle lc
               LEFT JOIN memory_fragments f ON lc.memory_id = CAST(f.id AS TEXT)
               WHERE lc.user_id = ? AND lc.lifecycle_status = 'soft_deleted'
               ORDER BY lc.soft_deleted_at DESC
               LIMIT ? OFFSET ?''',
            (user_id, limit, offset)
        )

        memories = [dict(r) for r in rows] if rows else []

        count_rows = db.execute(
            '''SELECT COUNT(*) as total FROM memory_lifecycle
               WHERE user_id = ? AND lifecycle_status = 'soft_deleted' ''',
            (user_id,)
        )
        total = count_rows[0]["total"] if count_rows else 0

        return {
            "success": True,
            "memories": memories,
            "count": len(memories),
            "total": total,
        }

    except Exception as e:
        logger.error(f"✗ 列出已删除记忆失败: {e}")
        return {"success": False, "error": str(e)}


def get_delete_log(
    user_id: int,
    limit: int = 50,
    offset: int = 0,
    workspace_id: Optional[int] = None,
) -> Dict[str, Any]:
    """
    获取删除操作审计日志。

    Args:
        user_id: 用户 ID
        limit: 返回条数
        offset: 偏移量

    Returns:
        审计日志列表
    """
    try:
        _ensure_lifecycle_tables()
        db = get_db_client()

        rows = db.execute(
            '''SELECT * FROM memory_delete_log
               WHERE user_id = ?
               ORDER BY created_at DESC
               LIMIT ? OFFSET ?''',
            (user_id, limit, offset)
        )
        logs = [dict(r) for r in rows] if rows else []

        count_rows = db.execute(
            'SELECT COUNT(*) as total FROM memory_delete_log WHERE user_id = ?',
            (user_id,)
        )
        total = count_rows[0]["total"] if count_rows else 0

        return {
            "success": True,
            "logs": logs,
            "count": len(logs),
            "total": total,
        }

    except Exception as e:
        logger.error(f"✗ 获取删除日志失败: {e}")
        return {"success": False, "error": str(e)}


def get_lifecycle_stats(user_id: int, workspace_id: Optional[int] = None) -> Dict[str, Any]:
    """
    获取生命周期统计信息。

    Args:
        user_id: 用户 ID

    Returns:
        统计信息字典
    """
    try:
        _ensure_lifecycle_tables()
        db = get_db_client()

        def _count(condition: str) -> int:
            rows = db.execute(
                f'SELECT COUNT(*) as cnt FROM memory_lifecycle WHERE user_id = ? AND {condition}',
                (user_id,)
            )
            return rows[0]["cnt"] if rows else 0

        active = _count("lifecycle_status = 'active'")
        cold = _count("lifecycle_status = 'cold'")
        archived = _count("lifecycle_status = 'archived'")
        soft_deleted = _count("lifecycle_status = 'soft_deleted'")

        # 按类型统计
        type_rows = db.execute(
            '''SELECT f.fragment_type, COUNT(*) as cnt
               FROM memory_fragments f
               WHERE f.user_id = ? AND f.lifecycle_status = 'active'
               GROUP BY f.fragment_type''',
            (user_id,)
        )
        by_type = {r["fragment_type"]: r["cnt"] for r in type_rows} if type_rows else {}

        return {
            "success": True,
            "stats": {
                "active": active,
                "cold": cold,
                "archived": archived,
                "soft_deleted": soft_deleted,
                "total": active + cold + archived + soft_deleted,
                "by_type": by_type,
            },
        }

    except Exception as e:
        logger.error(f"✗ 获取生命周期统计失败: {e}")
        return {"success": False, "error": str(e)}


# ============================================================
# 3. 自动合并与冲突检测
# ============================================================

def _text_similarity(text1: str, text2: str) -> float:
    """
    计算两段文本的相似度（基于 Jaccard 和字符重叠）。

    使用字符二元组（bigram）的 Jaccard 相似系数。

    Args:
        text1: 文本 1
        text2: 文本 2

    Returns:
        相似度（0.0 ~ 1.0）
    """
    if not text1 or not text2:
        return 0.0

    # 提取字符二元组
    def _bigrams(s: str) -> set:
        return {s[i:i + 2] for i in range(len(s) - 1)}

    bg1 = _bigrams(text1)
    bg2 = _bigrams(text2)

    if not bg1 or not bg2:
        return 0.0

    intersection = bg1 & bg2
    union = bg1 | bg2

    return len(intersection) / len(union)


_EXTRACTION_SYSTEM_PROMPT_FOR_MERGE = """你是一个记忆合并与冲突解决引擎。
请分析以下两条记忆，判断它们的关系：

- duplicate: 重复记忆（表达相同或高度相似的含义）
- conflict: 冲突记忆（指向同一事物但内容矛盾）
- compatible: 兼容记忆（不同维度，可以独立存在）

返回 JSON（不要包含其他文字）：
{"relation": "duplicate|conflict|compatible", "reason": "分析原因", "suggestion": "合并或解决建议"}"""


def _llm_judge_merge_conflict(
    user_id: int,
    mem1: Dict[str, Any],
    mem2: Dict[str, Any],
) -> Dict[str, Any]:
    """
    使用 LLM 判断两条记忆的关系（增强判断）。
    回退到基于相似度的规则判断。

    Args:
        user_id: 用户 ID
        mem1: 记忆 1
        mem2: 记忆 2

    Returns:
        判断结果
    """
    content1 = mem1.get("content", "")
    content2 = mem2.get("content", "")

    # 先走规则判断
    similarity = _text_similarity(content1, content2)

    if similarity >= 0.85:
        return {"relation": "duplicate", "similarity": similarity,
                "reason": f"文本高度相似 ({similarity:.2f})"}
    elif similarity >= 0.5:
        # 中等相似度，尝试 LLM 判断
        try:
            from app.services.llm_backend_service import llm_chat

            prompt = f"记忆A: {content1}\n记忆B: {content2}\n请判断它们的关系（duplicate/conflict/compatible）。"
            msg = [
                {"role": "system", "content": _EXTRACTION_SYSTEM_PROMPT_FOR_MERGE},
                {"role": "user", "content": prompt},
            ]
            result = llm_chat(user_id=user_id, messages=msg, temperature=0.1, enqueue_on_failure=True)
            if result.get("success"):
                import json as _json
                try:
                    text = result["content"]
                    import re
                    code_match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', text, re.DOTALL)
                    if code_match:
                        text = code_match.group(1).strip()
                    json_match = re.search(r'\{.*\}', text, re.DOTALL)
                    if json_match:
                        parsed = _json.loads(json_match.group(0))
                        parsed["similarity"] = similarity
                        return parsed
                except Exception:
                    pass
        except Exception:
            pass

        return {"relation": "compatible", "similarity": similarity,
                "reason": f"中等相似度 ({similarity:.2f})，需人工确认"}
    else:
        return {"relation": "compatible", "similarity": similarity,
                "reason": f"相似度较低 ({similarity:.2f})"}


def find_duplicates(
    user_id: int,
    content: str,
    threshold: float = 0.85,
    limit: int = 10,
    workspace_id: Optional[int] = None,
) -> Dict[str, Any]:
    """
    检测与指定内容重复的记忆。

    使用 bigram Jaccard 相似度算法。

    Args:
        user_id: 用户 ID
        content: 待检查的文本内容
        threshold: 相似度阈值（默认 0.85）
        limit: 返回结果上限

    Returns:
        重复检测结果
    """
    try:
        result = list_fragments(user_id, limit=200)
        all_fragments = result.get("fragments", [])

        duplicates = []
        for frag in all_fragments:
            frag_content = frag.get("content", "")
            if not frag_content:
                continue

            sim = _text_similarity(content, frag_content)
            if sim >= threshold:
                duplicates.append({
                    "id": frag.get("id"),
                    "content": frag_content,
                    "fragment_type": frag.get("fragment_type"),
                    "similarity": round(sim, 4),
                    "created_at": frag.get("created_at"),
                })

        duplicates.sort(key=lambda x: x["similarity"], reverse=True)

        return {
            "success": True,
            "duplicates": duplicates[:limit],
            "count": len(duplicates[:limit]),
            "threshold": threshold,
        }

    except Exception as e:
        logger.error(f"✗ 重复检测失败: {e}")
        return {"success": False, "error": str(e)}


def detect_conflicts(
    user_id: int,
    key: str,
    new_value: str,
    conflict_type: Optional[str] = None,
    workspace_id: Optional[int] = None,
) -> Dict[str, Any]:
    """
    检测记忆值冲突。

    当同一 key 已有不同值时触发冲突告警。
    例如已有 "user_company: 腾讯"，尝试设置 "user_company: 阿里" 时触发冲突。

    Args:
        user_id: 用户 ID
        key: 变量 key
        new_value: 新值
        conflict_type: 冲突类型（如 value_mismatch / source_conflict / time_conflict）

    Returns:
        冲突检测结果（包含持久化的 conflict_id）
    """
    try:
        existing = get_memory_variable(user_id, key)
        if existing is None:
            return {"success": True, "conflict": False,
                    "message": "无现有值，无冲突"}

        existing_str = str(existing) if existing is not None else ""
        new_str = str(new_value)

        # 如果值相同，不算冲突
        if existing_str == new_str:
            return {"success": True, "conflict": False,
                    "message": "值相同，无冲突"}

        similarity = _text_similarity(existing_str, new_str)

        # 也检查是否有其他变量引用了相同实体
        all_vars = list_memory_variables(user_id)
        related_conflicts = []
        if isinstance(all_vars, dict):
            for k, v in all_vars.items():
                if k != key and str(v) == new_str:
                    related_conflicts.append({
                        "key": k,
                        "existing_value": str(v),
                    })

        # 自动判断冲突类型
        if not conflict_type:
            conflict_type = "source_conflict" if related_conflicts else "value_mismatch"

        # 持久化冲突到 memory_merge_log
        _ensure_lifecycle_tables()
        db = get_db_client()
        conflict_id = db.execute(
            '''INSERT INTO memory_merge_log
               (user_id, memory_type, source_ids, target_id,
                merge_type, merge_action, similarity_score,
                old_value, new_value, conflict_type, operator, resolved)
               VALUES (?, ?, ?, ?, 'conflict', 'detected',
                ?, ?, ?, ?, 'system', 0)''',
            (user_id, "variable",
             json.dumps([key]), key,
             round(similarity, 4),
             existing_str, new_str, conflict_type)
        )

        logger.info(f"✓ 冲突持久化: 记录 ID={conflict_id}, key='{key}', "
                    f"'{existing_str}' → '{new_str}'")

        return {
            "success": True,
            "conflict": True,
            "conflict_id": conflict_id,
            "key": key,
            "existing_value": existing_str,
            "new_value": new_str,
            "similarity": round(similarity, 4),
            "related_conflicts": related_conflicts,
            "message": f"检测到值冲突: 现有 '{existing_str}' vs 新值 '{new_str}'",
        }

    except Exception as e:
        logger.error(f"✗ 冲突检测失败: {e}")
        return {"success": False, "error": str(e)}


def merge_memories(
    user_id: int,
    source_ids: List[int],
    target_content: str,
    target_type: str = "info",
    workspace_id: Optional[int] = None,
) -> Dict[str, Any]:
    """
    合并多条重复记忆。

    将 source_ids 中的记忆合并为一条新的记忆，
    并标记源记忆为 archived。

    Args:
        user_id: 用户 ID
        source_ids: 源记忆 ID 列表
        target_content: 合并后的内容
        target_type: 合并后的类型

    Returns:
        合并结果
    """
    try:
        _ensure_lifecycle_tables()
        db = get_db_client()

        if len(source_ids) < 2:
            return {"success": False, "error": "至少需要 2 条记忆才能合并"}

        # 1. 收集源记忆的原始内容（用于审计）
        old_values = []
        for sid in source_ids:
            frag_result = get_fragment(user_id, sid)
            if frag_result.get("success"):
                old_values.append({
                    "id": sid,
                    "content": frag_result["fragment"].get("content"),
                })

        # 2. 创建新的合并后记忆（通过 update_fragment 更新第一条为目标内容）
        first_id = source_ids[0]
        update_fragment(
            user_id=user_id,
            fragment_id=first_id,
            content=target_content,
        )

        # 3. 将其他源记忆标记为 archived
        for sid in source_ids[1:]:
            _upsert_lifecycle(
                user_id=user_id,
                memory_type="fragment",
                memory_id=str(sid),
                lifecycle_status="archived",
                archived_at=datetime.now().isoformat(),
                cold_reason="merged",
            )
            db.execute(
                '''UPDATE memory_fragments SET lifecycle_status = 'archived'
                   WHERE id = ?''',
                (sid,)
            )

        # 4. 记录合并审计日志
        _ensure_lifecycle_tables()
        db.execute(
            '''INSERT INTO memory_merge_log
               (user_id, memory_type, source_ids, target_id,
                merge_type, merge_action, similarity_score,
                old_value, new_value, resolved)
               VALUES (?, ?, ?, ?, 'duplicate', 'auto_merged',
                ?, ?, ?, 1)''',
            (user_id, "fragment",
             json.dumps(source_ids), str(first_id),
             0.95,
             json.dumps(old_values, ensure_ascii=False),
             target_content)
        )

        # 5. 记录版本
        try:
            record_version(
                user_id=user_id,
                memory_type="fragment",
                memory_id=str(first_id),
                action="merge",
                old_value=old_values,
                new_value=target_content,
            )
        except Exception:
            pass

        logger.info(f"✓ 合并记忆: {len(source_ids)} 条 → {first_id}")
        try:
            for sid in source_ids:
                record_trace_event(user_id, str(sid) if isinstance(sid, int) else sid, "fragment", "merged", "lifecycle",
                                  metadata={"target_id": str(first_id)})
        except Exception:
            pass
        return {
            "success": True,
            "target_id": first_id,
            "merged_count": len(source_ids),
            "message": f"Merged {len(source_ids)} memories into {first_id}",
        }

    except Exception as e:
        logger.error(f"✗ 合并记忆失败: {e}")
        return {"success": False, "error": str(e)}


def resolve_conflict(
    user_id: int,
    conflict_id: int,
    resolution: str,
    merged_value: Optional[str] = None,
    workspace_id: Optional[int] = None,
) -> Dict[str, Any]:
    """
    解决一条已检测到的记忆冲突，并将最终值写回记忆变量。

    Args:
        user_id: 用户 ID
        conflict_id: 冲突记录 ID（merge_log 表中的记录）
        resolution: 解决方式
            - accept_new / keep_new: 采用新值
            - keep_current / keep_old: 保留当前值
            - manual / merge: 手动合并
        merged_value: 手动合并时的最终值（manual 方式必填）

    Returns:
        解决结果
    """
    try:
        _ensure_lifecycle_tables()
        db = get_db_client()

        rows = db.execute(
            '''SELECT * FROM memory_merge_log WHERE id = ? AND user_id = ?''',
            (conflict_id, user_id)
        )
        if not rows:
            return {"success": False, "error": f"冲突记录 {conflict_id} 未找到"}

        row = dict(rows[0])

        # 规范化解决方式，兼容旧版 keep_new / keep_old / merge
        normalized = {
            "keep_new": "accept_new",
            "keep_old": "keep_current",
            "merge": "manual",
        }.get(resolution, resolution)

        valid_resolutions = {"accept_new", "keep_current", "manual"}
        if normalized not in valid_resolutions:
            return {"success": False, "error": f"无效的解决方式: {resolution}"}

        old_value = row.get("old_value") or ""
        new_value = row.get("new_value") or ""

        if normalized == "accept_new":
            final_value = new_value
        elif normalized == "keep_current":
            final_value = old_value
        else:
            final_value = merged_value if merged_value is not None else f"{old_value} / {new_value}"

        # 获取变量 key（优先 target_id，其次 source_ids 列表第一项）
        key = row.get("target_id")
        if not key:
            try:
                source_ids = json.loads(row.get("source_ids") or "[]")
                key = source_ids[0] if source_ids else None
            except Exception:
                key = None

        if key:
            set_memory_variable(user_id, key, final_value)

        now = datetime.now().isoformat()
        merge_action = f"resolved:{normalized}"
        db.execute(
            '''UPDATE memory_merge_log SET resolved = 1,
               resolved_at = ?, new_value = ?, merge_action = ?, operator = 'user'
               WHERE id = ?''',
            (now, final_value, merge_action, conflict_id)
        )

        logger.info(f"✓ 冲突已解决: ID={conflict_id}, key='{key}', "
                    f"resolution={normalized}, final_value='{final_value}'")

        return {
            "success": True,
            "message": f"冲突 {conflict_id} 已解决",
            "resolution": normalized,
            "final_value": final_value,
            "key": key,
        }

    except Exception as e:
        logger.error(f"✗ 解决冲突失败: {e}")
        return {"success": False, "error": str(e)}


def list_pending_conflicts(user_id: int, workspace_id: Optional[int] = None) -> Dict[str, Any]:
    """
    列出待处理的冲突记录。

    Args:
        user_id: 用户 ID

    Returns:
        待处理冲突列表
    """
    try:
        _ensure_lifecycle_tables()
        db = get_db_client()

        rows = db.execute(
            '''SELECT * FROM memory_merge_log
               WHERE user_id = ? AND merge_type = 'conflict' AND resolved = 0
               ORDER BY created_at DESC''',
            (user_id,)
        )
        conflicts = [dict(r) for r in rows] if rows else []

        return {
            "success": True,
            "conflicts": conflicts,
            "count": len(conflicts),
        }

    except Exception as e:
        logger.error(f"✗ 列出冲突失败: {e}")
        return {"success": False, "error": str(e)}


def list_merge_log(
    user_id: int,
    limit: int = 50,
    offset: int = 0,
    workspace_id: Optional[int] = None,
) -> Dict[str, Any]:
    """
    获取合并操作审计日志。

    Args:
        user_id: 用户 ID
        limit: 返回条数
        offset: 偏移量

    Returns:
        合并日志列表
    """
    try:
        _ensure_lifecycle_tables()
        db = get_db_client()

        rows = db.execute(
            '''SELECT * FROM memory_merge_log
               WHERE user_id = ?
               ORDER BY created_at DESC
               LIMIT ? OFFSET ?''',
            (user_id, limit, offset)
        )
        logs = [dict(r) for r in rows] if rows else []

        count_rows = db.execute(
            'SELECT COUNT(*) as total FROM memory_merge_log WHERE user_id = ?',
            (user_id,)
        )
        total = count_rows[0]["total"] if count_rows else 0

        return {
            "success": True,
            "logs": logs,
            "count": len(logs),
            "total": total,
        }

    except Exception as e:
        logger.error(f"✗ 获取合并日志失败: {e}")
        return {"success": False, "error": str(e)}


# ============================================================
# 测试
# ============================================================

def test_memory_lifecycle():
    """测试生命周期管理模块"""
    import time

    print("\n" + "=" * 60)
    print("测试 Memory Lifecycle 模块")
    print("=" * 60 + "\n")

    test_user_id = 999
    _ensure_lifecycle_tables()
    db = get_db_client()

    # 清理
    for tbl in ['memory_lifecycle', 'memory_delete_log', 'memory_merge_log']:
        db.execute(f'DELETE FROM {tbl} WHERE user_id = ?', (test_user_id,))
    db.execute("DELETE FROM memory_fragments WHERE user_id = ?", (test_user_id,))
    print("  清理完成\n")

    # ================================================================
    # 1. 半衰期测试
    # ================================================================
    print("--- 1. 差异化半衰期机制 ---\n")

    print("1.1 半衰期配置查询...")
    for ft in ["info", "plan", "preference", "unknown"]:
        info = get_half_life_info(ft)
        print(f"  {ft} -> {info['half_life_days']} 天 ({info['description']})")
    print("  ✓ 半衰期配置正确\n")

    print("1.2 衰减分数计算...")
    from datetime import timedelta
    now = datetime.now()
    test_cases = [
        ("info (永久)", now, None, 1.0),
        ("plan (90天, 当天)", now, 90, 1.0),
        ("plan (90天, 90天后)", now - timedelta(days=90), 90, 0.5),
        ("pref (1天, 1天后)", now - timedelta(days=1), 1, 0.5),
        ("pref (1天, 2天后)", now - timedelta(days=2), 1, 0.25),
    ]
    for label, created, half_life, expected in test_cases:
        score = calculate_decay_score(created, half_life)
        print(f"  {label}: decay={score:.4f} (expected~{expected})")
        assert abs(score - expected) < 0.01, f"{label}: {score} != {expected}"
    print("  ✓ 衰减计算正确\n")

    print("1.3 预估剩余存活时间...")
    frag = {
        "fragment_type": "plan",
        "created_at": (datetime.now() - timedelta(days=30)).isoformat(),
        "importance_score": 0.8,
    }
    life = estimate_remaining_life(frag)
    print(f"  plan 创建 30 天后: {life}")
    assert life["remaining_days"] > 0
    print("  ✓ 剩余寿命预估正确\n")

    print("1.4 过期清理...")
    from app.services.memory_fragment_service import create_fragment

    # 创建一个过期片段
    frag_result = create_fragment(
        test_user_id, "preference",
        "临时的兴趣（很快过期）",
        ttl=1,  # 1秒
    )
    frag_id = frag_result.get("fragment_id")
    assert frag_id is not None

    # 手动设置为已过期
    db.execute(
        "UPDATE memory_fragments SET expires_at = ? WHERE id = ?",
        ((datetime.now() - timedelta(minutes=1)).isoformat(), frag_id)
    )

    result = cleanup_expired_memories()
    print(f"  清理结果: {result.get('cleaned', 0)} 条")
    assert result["success"]
    print("  ✓ 过期清理完成\n")

    # ================================================================
    # 2. 弹性驱逐测试
    # ================================================================
    print("--- 2. 弹性驱逐策略 ---\n")

    print("2.1 标记冷记忆...")
    result = mark_cold(test_user_id, "fragment", str(frag_id), "test_cold")
    print(f"  标记结果: {result.get('success')}")
    assert result["success"]

    cold_list = list_cold_memories(test_user_id)
    print(f"  冷记忆数: {cold_list.get('count', 0)}")
    assert cold_list["count"] > 0
    print("  ✓ 标记冷记忆成功\n")

    print("2.2 软删除与恢复...")
    # 创建一个片段用于测试软删除
    frag_result = create_fragment(
        test_user_id, "info",
        "测试软删除的记忆内容",
        ttl=None,
        importance_score=0.9,
    )
    sd_id = frag_result.get("fragment_id")

    result = soft_delete(test_user_id, "fragment", str(sd_id), "test_delete")
    print(f"  软删除: {result.get('success')}")
    assert result["success"]

    deleted_list = list_deleted_memories(test_user_id)
    print(f"  已删除记忆数: {deleted_list.get('count', 0)}")
    assert deleted_list["count"] > 0

    result = restore_memory(test_user_id, "fragment", str(sd_id))
    print(f"  恢复: {result.get('success')}")
    assert result["success"]

    deleted_list = list_deleted_memories(test_user_id)
    print(f"  恢复后已删除数: {deleted_list.get('count', 0)}")
    assert deleted_list["count"] == 0
    print("  ✓ 软删除与恢复成功\n")

    print("2.3 硬删除...")
    result = hard_delete(test_user_id, "fragment", str(sd_id), "test_hard_delete")
    print(f"  硬删除: {result.get('success')}")
    assert result["success"]

    audit = get_delete_log(test_user_id)
    print(f"  审计日志数: {audit.get('count', 0)}")
    assert audit["count"] > 0
    print("  ✓ 硬删除与审计日志成功\n")

    print("2.4 生命周期统计...")
    stats = get_lifecycle_stats(test_user_id)
    print(f"  统计: {stats.get('stats', {})}")
    assert stats["success"]
    print("  ✓ 统计成功\n")

    print("2.5 自动归档冷记忆...")
    result = auto_archive_cold_memories(test_user_id, cold_days=0)  # 立即归档
    print(f"  归档: {result.get('archived', 0)} 条")
    print("  ✓ 自动归档成功\n")

    # ================================================================
    # 3. 合并与冲突测试
    # ================================================================
    print("--- 3. 自动合并与冲突检测 ---\n")

    print("3.1 文本相似度...")
    sim = _text_similarity("我喜欢极简设计", "我喜欢极简设计风格")
    print(f"  '我喜欢极简设计' vs '我喜欢极简设计风格': {sim:.4f}")
    assert sim > 0.5

    sim = _text_similarity("今天天气很好", "我记得你叫小明")
    print(f"  '今天天气很好' vs '我记得你叫小明': {sim:.4f}")
    assert sim < 0.5
    print("  ✓ 文本相似度正确\n")

    print("3.2 重复检测...")
    create_fragment(test_user_id, "info", "用户叫小明", ttl=None, importance_score=0.8)
    create_fragment(test_user_id, "info", "用户小明的信息", ttl=None, importance_score=0.7)

    dups = find_duplicates(test_user_id, "用户叫小明", threshold=0.5)
    print(f"  重复结果: {dups.get('count', 0)} 条")
    print("  ✓ 重复检测成功\n")

    print("3.3 冲突检测...")
    set_memory_variable(test_user_id, "test_company", "腾讯")
    conflict = detect_conflicts(test_user_id, "test_company", "阿里")
    print(f"  冲突检测: conflict={conflict.get('conflict')}")
    assert conflict["success"]
    assert conflict["conflict"] == True
    print(f"  现有值: {conflict.get('existing_value')} -> 新值: {conflict.get('new_value')}")

    no_conflict = detect_conflicts(test_user_id, "test_company_new", "新值")
    print(f"  无冲突: conflict={no_conflict.get('conflict')}")
    assert no_conflict["conflict"] == False
    print("  ✓ 冲突检测成功\n")

    print("3.4 合并记忆...")
    f1 = create_fragment(test_user_id, "info", "记忆A: 测试合并内容", ttl=None, importance_score=0.5)
    f2 = create_fragment(test_user_id, "info", "记忆B: 测试合并内容补充", ttl=None, importance_score=0.5)
    f1_id = f1.get("fragment_id")
    f2_id = f2.get("fragment_id")

    result = merge_memories(test_user_id, [f1_id, f2_id], "合并后的记忆内容")
    print(f"  合并: {result.get('success')}, target={result.get('target_id')}")
    assert result["success"]

    merge_log = list_merge_log(test_user_id)
    print(f"  合并日志数: {merge_log.get('count', 0)}")
    assert merge_log["count"] > 0
    print("  ✓ 合并记忆成功\n")

    # ================================================================
    # 4. 配置查询
    # ================================================================
    print("--- 4. 半衰期配置查询 ---\n")
    for ft in ["info", "plan", "preference", "unknown"]:
        cfg = get_half_life_info(ft)
        print(f"  {ft}: {cfg['half_life_days']} 天, 衰减={'是' if cfg['decay_enabled'] else '否'}")
    print("  ✓ 配置查询成功\n")

    # 清理
    print("--- 清理测试数据 ---")
    for tbl in ['memory_lifecycle', 'memory_delete_log', 'memory_merge_log']:
        db.execute(f'DELETE FROM {tbl} WHERE user_id = ?', (test_user_id,))
    db.execute("DELETE FROM memory_fragments WHERE user_id = ?", (test_user_id,))
    db.execute("DELETE FROM memory_variables WHERE user_id = ? AND key LIKE 'test_%'", (test_user_id,))
    print("  清理完成")

    print("\n" + "=" * 60)
    print("✅ Memory Lifecycle 模块测试完成！")
    print("=" * 60 + "\n")

    return True


# ============================================================
# 生命周期调度器
# ============================================================

_scheduler_task = None
_scheduler_running = False


async def _scheduled_maintenance_loop():
    """后台定时维护循环，每 6 小时执行一次归档+清理+去重扫描"""
    global _scheduler_running
    logger.info("🔄 生命周期调度器已启动，每 6 小时执行一次维护")
    while _scheduler_running:
        try:
            await asyncio.sleep(6 * 60 * 60)  # 6 hours
            if not _scheduler_running:
                break
            logger.info("🔄 开始执行定时维护...")
            run_maintenance_now()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"✗ 定时维护执行异常: {e}")
            await asyncio.sleep(60)  # 1 min 后重试
    logger.info("🔄 生命周期调度器已停止")


def start_lifecycle_scheduler():
    """启动生命周期调度器"""
    global _scheduler_task, _scheduler_running
    if _scheduler_running:
        logger.warning("生命周期调度器已在运行")
        return
    _scheduler_running = True
    try:
        _scheduler_task = asyncio.create_task(_scheduled_maintenance_loop())
        logger.info("✓ 生命周期调度器已启动，每 6 小时执行一次维护")
    except RuntimeError:
        _scheduler_running = False
        logger.warning("无事件循环，跳过生命周期调度器启动")


def stop_lifecycle_scheduler():
    """停止生命周期调度器"""
    global _scheduler_running, _scheduler_task
    _scheduler_running = False
    if _scheduler_task:
        _scheduler_task.cancel()
        _scheduler_task = None
    logger.info("生命周期调度器已停止")


def run_maintenance_now() -> Dict[str, Any]:
    """
    同步手动触发维护（归档冷记忆 + 清理过期 + 去重扫描）。

    Returns:
        维护结果统计
    """
    results = {}

    # 1. 归档冷记忆
    try:
        archive_result = auto_archive_cold_memories()
        results["archive"] = archive_result
        logger.info(f"归档冷记忆: {archive_result.get('archived', 0)} 条")
    except Exception as e:
        results["archive"] = {"success": False, "error": str(e)}
        logger.error(f"✗ 归档冷记忆失败: {e}")

    # 2. 清理过期记忆
    try:
        cleanup_result = cleanup_expired_memories()
        results["cleanup"] = cleanup_result
        logger.info(f"清理过期记忆: {cleanup_result.get('cleaned', 0)} 条")
    except Exception as e:
        results["cleanup"] = {"success": False, "error": str(e)}
        logger.error(f"✗ 清理过期记忆失败: {e}")

    # 3. 去重扫描
    try:
        dedup_results = _scan_duplicates_all_users()
        results["dedup_scan"] = dedup_results
        logger.info(f"去重扫描: 检测到 {dedup_results.get('duplicates_found', 0)} 组重复")
    except Exception as e:
        results["dedup_scan"] = {"success": False, "error": str(e)}
        logger.error(f"✗ 去重扫描失败: {e}")

    results["success"] = True
    results["timestamp"] = datetime.now().isoformat()
    return results


def _scan_duplicates_all_users() -> Dict[str, Any]:
    """扫描所有用户的重复记忆（仅检测，不删除）"""
    try:
        db = get_db_client()
        users = db.execute('SELECT DISTINCT user_id FROM memory_fragments LIMIT 50')
        if not users:
            return {"success": True, "duplicates_found": 0, "users_scanned": 0}

        total_duplicates = 0
        users_with_dups = 0

        for user_row in users:
            user_id = dict(user_row)["user_id"]
            frags_result = list_fragments(user_id, limit=50)
            fragments = frags_result.get("fragments", [])
            user_dup_count = 0

            for frag in fragments:
                content = frag.get("content", "")
                if not content:
                    continue
                dup_result = find_duplicates(user_id, content, threshold=0.85, limit=5)
                if dup_result.get("success") and dup_result.get("count", 0) > 0:
                    user_dup_count += dup_result["count"]

            if user_dup_count > 0:
                users_with_dups += 1
                total_duplicates += user_dup_count
                logger.info(f"用户 {user_id} 检测到 {user_dup_count} 条重复记忆")

        return {
            "success": True,
            "duplicates_found": total_duplicates,
            "users_scanned": len(users),
            "users_with_duplicates": users_with_dups,
        }
    except Exception as e:
        logger.error(f"✗ 去重扫描失败: {e}")
        return {"success": False, "error": str(e)}


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    test_memory_lifecycle()

