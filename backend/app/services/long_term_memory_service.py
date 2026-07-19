"""
长期记忆管理服务

实现记忆版本控制、自我改进算法、长期记忆管理 API 后端逻辑
"""
import logging
import json
import math
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

from app.core.db_client import get_db_client
from app.services.memory_fragment_service import (
    list_fragments,
    get_fragment,
    update_fragment,
    delete_fragment,
    create_fragment,
)
from app.services.memory_variable_service import (
    list_memory_variables,
    get_memory_variable,
    set_memory_variable,
    delete_memory_variable,
)
from app.services.memory_table_service import list_tables


# ============================================================
# Task 22: 记忆版本控制机制
# ============================================================

def _ensure_version_tables():
    """确保版本控制表存在"""
    db = get_db_client()
    db.execute('''
        CREATE TABLE IF NOT EXISTS memory_versions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            memory_type TEXT NOT NULL,
            memory_id TEXT NOT NULL,
            version INTEGER NOT NULL,
            action TEXT NOT NULL,
            old_value TEXT,
            new_value TEXT,
            changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS memory_feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            memory_type TEXT NOT NULL,
            memory_id TEXT NOT NULL,
            feedback_type TEXT NOT NULL,
            feedback_value REAL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    ''')
    db.execute('CREATE INDEX IF NOT EXISTS idx_versions_user ON memory_versions(user_id)')
    db.execute('CREATE INDEX IF NOT EXISTS idx_feedback_user ON memory_feedback(user_id)')


def record_version(user_id: int,
                   memory_type: str,
                   memory_id: str,
                   action: str,
                   old_value: Any = None,
                   new_value: Any = None,
                   workspace_id: Optional[int] = None) -> Dict[str, Any]:
    """
    记录记忆版本变更
    
    Args:
        user_id: 用户 ID
        memory_type: 记忆类型（variable, fragment, table_record）
        memory_id: 记忆 ID（key 或 fragment_id）
        action: 操作类型（create, update, delete）
        old_value: 旧值
        new_value: 新值
        
    Returns:
        记录结果
    """
    try:
        _ensure_version_tables()
        db = get_db_client()
        
        # 获取当前最大版本号
        rows = db.execute(
            'SELECT MAX(version) as max_version FROM memory_versions WHERE user_id = ? AND memory_type = ? AND memory_id = ?',
            (user_id, memory_type, memory_id)
        )
        current_version = rows[0]["max_version"] if rows and rows[0]["max_version"] else 0
        new_version = current_version + 1
        
        # 序列化值
        old_str = json.dumps(old_value, ensure_ascii=False) if old_value is not None else None
        new_str = json.dumps(new_value, ensure_ascii=False) if new_value is not None else None
        
        db.execute(
            'INSERT INTO memory_versions (user_id, memory_type, memory_id, version, action, old_value, new_value) VALUES (?, ?, ?, ?, ?, ?, ?)',
            (user_id, memory_type, memory_id, new_version, action, old_str, new_str)
        )
        
        return {
            "success": True,
            "version": new_version,
            "message": f"Version {new_version} recorded for {memory_type}:{memory_id}"
        }
        
    except Exception as e:
        logger.error(f"✗ 记录版本失败: {e}")
        return {"success": False, "error": str(e)}


def get_version_history(user_id: int,
                        memory_type: str,
                        memory_id: str,
                        workspace_id: Optional[int] = None) -> Dict[str, Any]:
    """
    获取记忆的版本历史
    
    Args:
        user_id: 用户 ID
        memory_type: 记忆类型
        memory_id: 记忆 ID
        
    Returns:
        版本历史列表
    """
    try:
        _ensure_version_tables()
        db = get_db_client()
        
        rows = db.execute(
            'SELECT * FROM memory_versions WHERE user_id = ? AND memory_type = ? AND memory_id = ? ORDER BY version DESC',
            (user_id, memory_type, memory_id)
        )
        
        versions = []
        if rows:
            for row in rows:
                v = dict(row)
                if v.get("old_value"):
                    try:
                        v["old_value"] = json.loads(v["old_value"])
                    except json.JSONDecodeError:
                        pass
                if v.get("new_value"):
                    try:
                        v["new_value"] = json.loads(v["new_value"])
                    except json.JSONDecodeError:
                        pass
                versions.append(v)
        
        return {
            "success": True,
            "versions": versions,
            "count": len(versions),
            "memory_type": memory_type,
            "memory_id": memory_id
        }
        
    except Exception as e:
        logger.error(f"✗ 获取版本历史失败: {e}")
        return {"success": False, "error": str(e)}


def rollback_to_version(user_id: int,
                        memory_type: str,
                        memory_id: str,
                        target_version: int,
                        workspace_id: Optional[int] = None) -> Dict[str, Any]:
    """
    回滚到指定版本
    
    Args:
        user_id: 用户 ID
        memory_type: 记忆类型
        memory_id: 记忆 ID
        target_version: 目标版本号
        
    Returns:
        回滚结果
    """
    try:
        _ensure_version_tables()
        db = get_db_client()
        
        # 获取目标版本的数据
        rows = db.execute(
            'SELECT * FROM memory_versions WHERE user_id = ? AND memory_type = ? AND memory_id = ? AND version = ?',
            (user_id, memory_type, memory_id, target_version)
        )
        
        if not rows:
            return {"success": False, "error": f"Version {target_version} not found"}
        
        target = dict(rows[0])
        
        # 根据操作类型恢复
        if target["action"] == "create":
            # 恢复到创建时的状态
            new_value = json.loads(target["new_value"]) if target.get("new_value") else None
            if memory_type == "variable":
                set_memory_variable(user_id, memory_id, new_value)
            elif memory_type == "fragment":
                update_fragment(user_id, int(memory_id), content=str(new_value))
        elif target["action"] == "update":
            # 恢复到更新前的状态
            old_value = json.loads(target["old_value"]) if target.get("old_value") else None
            if memory_type == "variable":
                set_memory_variable(user_id, memory_id, old_value)
            elif memory_type == "fragment":
                update_fragment(user_id, int(memory_id), content=str(old_value))
        elif target["action"] == "delete":
            # 恢复被删除的数据
            old_value = json.loads(target["old_value"]) if target.get("old_value") else None
            if memory_type == "variable":
                set_memory_variable(user_id, memory_id, old_value)
        
        # 记录回滚操作
        record_version(user_id, memory_type, memory_id, "rollback", None, f"Rolled back to version {target_version}")
        
        return {
            "success": True,
            "rolled_back_to": target_version,
            "message": f"Rolled back {memory_type}:{memory_id} to version {target_version}"
        }
        
    except Exception as e:
        logger.error(f"✗ 回滚失败: {e}")
        return {"success": False, "error": str(e)}


def get_audit_log(user_id: int,
                  memory_type: Optional[str] = None,
                  limit: int = 50,
                  offset: int = 0,
                  workspace_id: Optional[int] = None) -> Dict[str, Any]:
    """
    获取记忆变更审计日志
    
    Args:
        user_id: 用户 ID
        memory_type: 记忆类型过滤（可选）
        limit: 返回数量
        offset: 偏移量
        
    Returns:
        审计日志列表
    """
    try:
        _ensure_version_tables()
        db = get_db_client()
        
        if memory_type:
            rows = db.execute(
                'SELECT * FROM memory_versions WHERE user_id = ? AND memory_type = ? ORDER BY changed_at DESC LIMIT ? OFFSET ?',
                (user_id, memory_type, limit, offset)
            )
            count_rows = db.execute(
                'SELECT COUNT(*) as total FROM memory_versions WHERE user_id = ? AND memory_type = ?',
                (user_id, memory_type)
            )
        else:
            rows = db.execute(
                'SELECT * FROM memory_versions WHERE user_id = ? ORDER BY changed_at DESC LIMIT ? OFFSET ?',
                (user_id, limit, offset)
            )
            count_rows = db.execute(
                'SELECT COUNT(*) as total FROM memory_versions WHERE user_id = ?',
                (user_id,)
            )
        
        logs = [dict(row) for row in rows] if rows else []
        total = count_rows[0]["total"] if count_rows else 0
        
        return {
            "success": True,
            "logs": logs,
            "count": len(logs),
            "total": total,
            "limit": limit,
            "offset": offset
        }
        
    except Exception as e:
        logger.error(f"✗ 获取审计日志失败: {e}")
        return {"success": False, "error": str(e)}


# ============================================================
# Task 23: 自我改进算法（Self-improving）
# ============================================================

def submit_feedback(user_id: int,
                    memory_type: str,
                    memory_id: str,
                    feedback_type: str,
                    feedback_value: float = 1.0,
                    workspace_id: Optional[int] = None) -> Dict[str, Any]:
    """
    提交用户对记忆的反馈
    
    Args:
        user_id: 用户 ID
        memory_type: 记忆类型
        memory_id: 记忆 ID
        feedback_type: 反馈类型（positive, negative）
        feedback_value: 反馈强度（-1.0 到 1.0）
        
    Returns:
        提交结果
    """
    try:
        _ensure_version_tables()
        db = get_db_client()
        
        # 调整反馈值
        if feedback_type == "positive":
            feedback_value = abs(feedback_value)
        elif feedback_type == "negative":
            feedback_value = -abs(feedback_value)
        
        # 记录反馈
        db.execute(
            'INSERT INTO memory_feedback (user_id, memory_type, memory_id, feedback_type, feedback_value) VALUES (?, ?, ?, ?, ?)',
            (user_id, memory_type, memory_id, feedback_type, feedback_value)
        )
        
        # 调整记忆重要性
        if memory_type == "fragment":
            fragment = get_fragment(user_id, int(memory_id))
            if fragment["success"]:
                current_score = fragment["fragment"].get("importance_score", 0.5)
                # 正反馈提升重要性，负反馈降低
                adjustment = feedback_value * 0.1
                new_score = max(0.0, min(1.0, current_score + adjustment))
                update_fragment(user_id, int(memory_id), importance_score=new_score)
        
        logger.info(f"✓ 记录反馈: {memory_type}:{memory_id} -> {feedback_type}({feedback_value})")
        
        return {
            "success": True,
            "feedback_type": feedback_type,
            "feedback_value": feedback_value,
            "message": f"Feedback recorded for {memory_type}:{memory_id}"
        }
        
    except Exception as e:
        logger.error(f"✗ 提交反馈失败: {e}")
        return {"success": False, "error": str(e)}


def auto_adjust_importance(user_id: int, workspace_id: Optional[int] = None) -> Dict[str, Any]:
    """
    自动调整所有记忆片段的重要性评分
    
    基于用户反馈历史自动调整
    
    Args:
        user_id: 用户 ID
        
    Returns:
        调整结果
    """
    try:
        _ensure_version_tables()
        db = get_db_client()
        
        # 获取所有反馈
        feedback_rows = db.execute(
            'SELECT memory_id, SUM(feedback_value) as total_feedback, COUNT(*) as feedback_count FROM memory_feedback WHERE user_id = ? AND memory_type = ? GROUP BY memory_id',
            (user_id, "fragment")
        )
        
        adjustments = []
        if feedback_rows:
            for row in feedback_rows:
                fragment_id = int(row["memory_id"])
                total_feedback = row["total_feedback"]
                feedback_count = row["feedback_count"]
                
                # 获取当前片段
                fragment = get_fragment(user_id, fragment_id)
                if not fragment["success"]:
                    continue
                
                current_score = fragment["fragment"].get("importance_score", 0.5)
                
                # 计算调整：基于反馈总量和频次
                # 正反馈多 -> 提升，负反馈多 -> 降低
                feedback_factor = total_feedback / max(feedback_count, 1)
                adjustment = feedback_factor * 0.15
                
                new_score = max(0.0, min(1.0, current_score + adjustment))
                
                # 更新重要性
                if abs(new_score - current_score) > 0.01:
                    update_fragment(user_id, fragment_id, importance_score=new_score)
                    adjustments.append({
                        "fragment_id": fragment_id,
                        "old_score": current_score,
                        "new_score": new_score,
                        "feedback_count": feedback_count,
                        "total_feedback": total_feedback
                    })
        
        logger.info(f"✓ 自动调整 {len(adjustments)} 个片段的重要性评分")
        
        return {
            "success": True,
            "adjusted_count": len(adjustments),
            "adjustments": adjustments,
            "message": f"Auto-adjusted {len(adjustments)} fragments"
        }
        
    except Exception as e:
        logger.error(f"✗ 自动调整失败: {e}")
        return {"success": False, "error": str(e)}


def get_self_improvement_stats(user_id: int, workspace_id: Optional[int] = None) -> Dict[str, Any]:
    """
    获取自我改进效果统计
    
    Args:
        user_id: 用户 ID
        
    Returns:
        统计数据
    """
    try:
        _ensure_version_tables()
        db = get_db_client()
        
        # 反馈统计
        feedback_stats = db.execute(
            'SELECT feedback_type, COUNT(*) as count, AVG(feedback_value) as avg_value FROM memory_feedback WHERE user_id = ? GROUP BY feedback_type',
            (user_id,)
        )
        
        feedback_by_type = {}
        if feedback_stats:
            for row in feedback_stats:
                feedback_by_type[row["feedback_type"]] = {
                    "count": row["count"],
                    "avg_value": round(row["avg_value"], 3) if row["avg_value"] else 0
                }
        
        # 版本变更统计
        version_stats = db.execute(
            'SELECT action, COUNT(*) as count FROM memory_versions WHERE user_id = ? GROUP BY action',
            (user_id,)
        )
        
        version_by_action = {}
        if version_stats:
            for row in version_stats:
                version_by_action[row["action"]] = row["count"]
        
        # 重要性分布
        fragments_result = list_fragments(user_id)
        importance_distribution = {"high": 0, "medium": 0, "low": 0}
        if fragments_result["success"]:
            for f in fragments_result["fragments"]:
                score = f.get("importance_score", 0.5)
                if score >= 0.7:
                    importance_distribution["high"] += 1
                elif score >= 0.4:
                    importance_distribution["medium"] += 1
                else:
                    importance_distribution["low"] += 1
        
        return {
            "success": True,
            "stats": {
                "feedback": feedback_by_type,
                "version_changes": version_by_action,
                "importance_distribution": importance_distribution,
                "total_feedback": sum(v["count"] for v in feedback_by_type.values()),
                "total_versions": sum(version_by_action.values())
            }
        }
        
    except Exception as e:
        logger.error(f"✗ 获取统计失败: {e}")
        return {"success": False, "error": str(e)}


# ============================================================
# Task 24: 长期记忆管理 API 后端逻辑
# ============================================================

def get_all_memories(user_id: int,
                     memory_type: Optional[str] = None,
                     sort_by: str = "importance",
                     sort_order: str = "DESC",
                     limit: int = 50,
                     offset: int = 0,
                     workspace_id: Optional[int] = None) -> Dict[str, Any]:
    """
    获取所有长期记忆（支持分页、过滤、排序）
    
    Args:
        user_id: 用户 ID
        memory_type: 记忆类型过滤（variable, fragment, table）
        sort_by: 排序字段（importance, created_at, updated_at）
        sort_order: 排序顺序
        limit: 返回数量
        offset: 偏移量
        
    Returns:
        所有记忆列表
    """
    try:
        all_memories = []
        
        # 1. 收集记忆变量
        if memory_type is None or memory_type == "variable":
            variables = list_memory_variables(user_id)
            if isinstance(variables, dict):
                for key, value in variables.items():
                    all_memories.append({
                        "type": "variable",
                        "id": key,
                        "content": f"{key}: {value}",
                        "key": key,
                        "value": value,
                        "importance_score": 0.5,
                        "created_at": None
                    })
        
        # 2. 收集记忆片段
        if memory_type is None or memory_type == "fragment":
            fragments_result = list_fragments(user_id, limit=1000)
            if fragments_result["success"]:
                for f in fragments_result["fragments"]:
                    all_memories.append({
                        "type": "fragment",
                        "id": str(f["id"]),
                        "content": f.get("content", ""),
                        "fragment_type": f.get("fragment_type", ""),
                        "importance_score": f.get("importance_score", 0.5),
                        "created_at": f.get("created_at"),
                        "expires_at": f.get("expires_at")
                    })
        
        # 3. 收集记忆表
        if memory_type is None or memory_type == "table":
            tables_result = list_tables(user_id)
            if tables_result["success"]:
                for t in tables_result.get("tables", []):
                    all_memories.append({
                        "type": "table",
                        "id": t.get("table_name", ""),
                        "content": f"Table: {t.get('table_name', '')} ({len(t.get('fields', []))} fields)",
                        "table_name": t.get("table_name"),
                        "fields": t.get("fields", []),
                        "importance_score": 0.5,
                        "created_at": t.get("created_at")
                    })
        
        # 4. 排序
        reverse = (sort_order.upper() == "DESC")
        if sort_by == "importance":
            all_memories.sort(key=lambda x: x.get("importance_score", 0), reverse=reverse)
        elif sort_by == "created_at":
            all_memories.sort(key=lambda x: x.get("created_at") or "", reverse=reverse)
        
        # 5. 分页
        total = len(all_memories)
        paginated = all_memories[offset:offset + limit]
        
        return {
            "success": True,
            "memories": paginated,
            "count": len(paginated),
            "total": total,
            "limit": limit,
            "offset": offset,
            "sort_by": sort_by,
            "sort_order": sort_order
        }
        
    except Exception as e:
        logger.error(f"✗ 获取所有记忆失败: {e}")
        return {"success": False, "error": str(e)}


def batch_delete_memories(user_id: int,
                          memory_ids: List[Dict[str, str]],
                          workspace_id: Optional[int] = None) -> Dict[str, Any]:
    """
    批量删除记忆
    
    Args:
        user_id: 用户 ID
        memory_ids: 要删除的记忆列表（格式：[{"type": "fragment", "id": "1"}, ...]）
        
    Returns:
        删除结果
    """
    try:
        successful = []
        failed = []
        
        for item in memory_ids:
            mem_type = item.get("type")
            mem_id = item.get("id")
            
            try:
                if mem_type == "variable":
                    delete_memory_variable(user_id, mem_id)
                    record_version(user_id, "variable", mem_id, "delete", mem_id, None)
                    successful.append({"type": mem_type, "id": mem_id})
                elif mem_type == "fragment":
                    # 记录删除前的值
                    fragment = get_fragment(user_id, int(mem_id))
                    if fragment["success"]:
                        old_content = fragment["fragment"].get("content")
                        record_version(user_id, "fragment", mem_id, "delete", old_content, None)
                    delete_fragment(user_id, int(mem_id))
                    successful.append({"type": mem_type, "id": mem_id})
                else:
                    failed.append({"type": mem_type, "id": mem_id, "error": "Unknown type"})
            except Exception as e:
                failed.append({"type": mem_type, "id": mem_id, "error": str(e)})
        
        return {
            "success": True,
            "deleted_count": len(successful),
            "successful": successful,
            "failed_count": len(failed),
            "failed": failed,
            "message": f"Deleted {len(successful)} memories"
        }
        
    except Exception as e:
        logger.error(f"✗ 批量删除失败: {e}")
        return {"success": False, "error": str(e)}


def adjust_memory_weight(user_id: int,
                         memory_type: str,
                         memory_id: str,
                         new_weight: float,
                         workspace_id: Optional[int] = None) -> Dict[str, Any]:
    """
    手动调整记忆权重和重要性评分
    
    Args:
        user_id: 用户 ID
        memory_type: 记忆类型
        memory_id: 记忆 ID
        new_weight: 新权重（0.0 - 1.0）
        
    Returns:
        调整结果
    """
    try:
        new_weight = max(0.0, min(1.0, new_weight))
        
        if memory_type == "fragment":
            # 记录旧值
            fragment = get_fragment(user_id, int(memory_id))
            if fragment["success"]:
                old_score = fragment["fragment"].get("importance_score", 0.5)
                record_version(user_id, "fragment", memory_id, "update", old_score, new_weight)
                update_fragment(user_id, int(memory_id), importance_score=new_weight)
            else:
                return {"success": False, "error": "Fragment not found"}
        else:
            return {"success": False, "error": f"Cannot adjust weight for type: {memory_type}"}
        
        return {
            "success": True,
            "memory_type": memory_type,
            "memory_id": memory_id,
            "new_weight": new_weight,
            "message": f"Weight adjusted to {new_weight}"
        }
        
    except Exception as e:
        logger.error(f"✗ 调整权重失败: {e}")
        return {"success": False, "error": str(e)}


# ============================================================
# 测试函数
# ============================================================

def test_long_term_memory():
    """测试长期记忆管理服务"""
    print("\n" + "="*60)
    print("测试长期记忆管理服务")
    print("="*60 + "\n")
    
    user_id = 999
    
    # 清理
    db = get_db_client()
    _ensure_version_tables()
    db.execute('DELETE FROM memory_versions WHERE user_id = ?', (user_id,))
    db.execute('DELETE FROM memory_feedback WHERE user_id = ?', (user_id,))
    db.execute('DELETE FROM memory_fragments WHERE user_id = ?', (user_id,))
    
    # Task 22: 版本控制
    print("--- Task 22: 记忆版本控制 ---\n")
    
    print("1. 测试版本记录...")
    result = record_version(user_id, "variable", "test_key", "create", None, "test_value")
    print(f"   版本: {result.get('version')}")
    assert result["success"] == True
    
    result = record_version(user_id, "variable", "test_key", "update", "test_value", "new_value")
    print(f"   版本: {result.get('version')}")
    assert result["success"] == True
    print(f"   ✓ 版本记录成功\n")
    
    print("2. 测试获取版本历史...")
    result = get_version_history(user_id, "variable", "test_key")
    print(f"   版本数: {result.get('count', 0)}")
    for v in result.get("versions", []):
        print(f"   - v{v['version']}: {v['action']} ({v.get('changed_at', '')})")
    assert result["count"] == 2
    print(f"   ✓ 版本历史获取成功\n")
    
    print("3. 测试审计日志...")
    result = get_audit_log(user_id)
    print(f"   日志数: {result.get('count', 0)}")
    assert result["count"] >= 2
    print(f"   ✓ 审计日志获取成功\n")
    
    # Task 23: 自我改进
    print("--- Task 23: 自我改进算法 ---\n")
    
    # 创建测试片段
    print("4. 准备测试片段...")
    frag1 = create_fragment(user_id, "preference", "喜欢深色模式", importance_score=0.5)
    frag2 = create_fragment(user_id, "info", "使用Python开发", importance_score=0.5)
    assert frag1["success"] and frag2["success"]
    frag_id_1 = str(frag1["fragment_id"])
    frag_id_2 = str(frag2["fragment_id"])
    print(f"   片段1 ID: {frag_id_1}, 片段2 ID: {frag_id_2}")
    
    print("5. 测试提交反馈...")
    result = submit_feedback(user_id, "fragment", frag_id_1, "positive", 1.0)
    print(f"   正反馈结果: {result.get('success')}")
    assert result["success"] == True
    
    result = submit_feedback(user_id, "fragment", frag_id_2, "negative", 1.0)
    print(f"   负反馈结果: {result.get('success')}")
    assert result["success"] == True
    print(f"   ✓ 反馈提交成功\n")
    
    print("6. 测试自动调整重要性...")
    result = auto_adjust_importance(user_id)
    print(f"   调整数: {result.get('adjusted_count', 0)}")
    for adj in result.get("adjustments", []):
        print(f"   - 片段 {adj['fragment_id']}: {adj['old_score']:.3f} -> {adj['new_score']:.3f}")
    assert result["success"] == True
    print(f"   ✓ 自动调整成功\n")
    
    print("7. 测试自我改进统计...")
    result = get_self_improvement_stats(user_id)
    print(f"   反馈统计: {result['stats']['feedback']}")
    print(f"   版本变更: {result['stats']['version_changes']}")
    print(f"   重要性分布: {result['stats']['importance_distribution']}")
    assert result["success"] == True
    print(f"   ✓ 统计获取成功\n")
    
    # Task 24: 长期记忆管理
    print("--- Task 24: 长期记忆管理 ---\n")
    
    print("8. 测试获取所有记忆...")
    result = get_all_memories(user_id, sort_by="importance", sort_order="DESC", limit=10)
    print(f"   记忆总数: {result.get('total', 0)}")
    print(f"   返回数: {result.get('count', 0)}")
    for m in result.get("memories", []):
        print(f"   - [{m['type']}] {m.get('content', '')[:30]}... (重要性: {m.get('importance_score', 0):.2f})")
    assert result["success"] == True
    print(f"   ✓ 获取所有记忆成功\n")
    
    print("9. 测试按类型过滤...")
    result = get_all_memories(user_id, memory_type="fragment")
    print(f"   片段数: {result.get('count', 0)}")
    assert all(m["type"] == "fragment" for m in result.get("memories", []))
    print(f"   ✓ 类型过滤成功\n")
    
    print("10. 测试调整权重...")
    result = adjust_memory_weight(user_id, "fragment", frag_id_1, 0.95)
    print(f"   调整结果: {result.get('success')}")
    assert result["success"] == True
    print(f"   ✓ 权重调整成功\n")
    
    # 清理
    print("--- 清理测试数据 ---")
    db.execute('DELETE FROM memory_versions WHERE user_id = ?', (user_id,))
    db.execute('DELETE FROM memory_feedback WHERE user_id = ?', (user_id,))
    db.execute('DELETE FROM memory_fragments WHERE user_id = ?', (user_id,))
    print("   ✓ 清理完成")
    
    print("\n" + "="*60)
    print("✅ 长期记忆管理服务测试完成！")
    print("="*60 + "\n")
    
    return True


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    test_long_term_memory()
