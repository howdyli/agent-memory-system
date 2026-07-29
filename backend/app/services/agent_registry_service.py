"""
Agent 注册服务（P1 R-12 跨 Agent 共享记忆作用域）

Agent 是轻量注册实体（不含独立认证），身份仍由 Principal 承担，
agent_id 仅作为记忆作用域标签（memory_fragments.agent_id/scope）。
"""
import logging
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

from app.core.db_client import get_db_client


def _workspace_clause(workspace_id: Optional[int]) -> tuple:
    """构造 workspace 过滤条件（NULL 表示个人空间）"""
    if workspace_id is None:
        return "workspace_id IS NULL", ()
    return "workspace_id = ?", (workspace_id,)


def register_agent(user_id: int,
                   name: str,
                   description: Optional[str] = None,
                   workspace_id: Optional[int] = None) -> Dict[str, Any]:
    """
    注册 Agent（同一 workspace 内 name 唯一）

    Returns:
        创建结果（duplicate=True 表示重名）
    """
    try:
        name = (name or "").strip()
        if not name:
            return {"success": False, "error": "Agent name is required"}

        db = get_db_client()
        ws_clause, ws_params = _workspace_clause(workspace_id)

        # 重名检查（UNIQUE(workspace_id, name) 对 NULL workspace 不生效，代码层兜底）
        existing = db.execute(
            f'SELECT id FROM agents WHERE {ws_clause} AND name = ?',
            (*ws_params, name)
        )
        if existing:
            return {"success": False, "duplicate": True,
                    "error": f"Agent '{name}' already exists in this workspace"}

        agent_id = db.execute(
            'INSERT INTO agents (workspace_id, user_id, name, description) VALUES (?, ?, ?, ?)',
            (workspace_id, user_id, name, description)
        )
        logger.info(f"✓ 注册 Agent: id={agent_id}, name='{name}', workspace={workspace_id}")
        return {
            "success": True,
            "agent": {
                "id": agent_id,
                "workspace_id": workspace_id,
                "user_id": user_id,
                "name": name,
                "description": description,
            },
        }
    except Exception as e:
        logger.error(f"✗ 注册 Agent 失败: {e}")
        return {"success": False, "error": str(e)}


def list_agents(user_id: int, workspace_id: Optional[int] = None) -> Dict[str, Any]:
    """列出当前 workspace（或个人空间）的 Agents"""
    try:
        db = get_db_client()
        if workspace_id is None:
            rows = db.execute(
                'SELECT * FROM agents WHERE workspace_id IS NULL AND user_id = ? ORDER BY id ASC',
                (user_id,)
            )
        else:
            rows = db.execute(
                'SELECT * FROM agents WHERE workspace_id = ? ORDER BY id ASC',
                (workspace_id,)
            )
        agents = [dict(r) for r in rows] if rows else []
        return {"success": True, "agents": agents, "count": len(agents)}
    except Exception as e:
        logger.error(f"✗ 列出 Agents 失败: {e}")
        return {"success": False, "error": str(e)}


def get_agent(agent_id: int, user_id: int, workspace_id: Optional[int] = None) -> Dict[str, Any]:
    """获取单个 Agent（限定当前 workspace/个人空间）"""
    try:
        db = get_db_client()
        if workspace_id is None:
            rows = db.execute(
                'SELECT * FROM agents WHERE id = ? AND workspace_id IS NULL AND user_id = ?',
                (agent_id, user_id)
            )
        else:
            rows = db.execute(
                'SELECT * FROM agents WHERE id = ? AND workspace_id = ?',
                (agent_id, workspace_id)
            )
        if not rows:
            return {"success": False, "not_found": True, "error": f"Agent {agent_id} not found"}
        return {"success": True, "agent": dict(rows[0])}
    except Exception as e:
        logger.error(f"✗ 获取 Agent 失败: {e}")
        return {"success": False, "error": str(e)}


def delete_agent(agent_id: int, user_id: int, workspace_id: Optional[int] = None) -> Dict[str, Any]:
    """
    删除 Agent。

    不删除记忆：该 Agent 的 private 记忆改为 scope='shared'，避免数据丢失/孤儿不可见。
    """
    try:
        found = get_agent(agent_id, user_id, workspace_id)
        if not found["success"]:
            return found

        db = get_db_client()
        # private 记忆转为 shared（保留数据，全局可见）
        shared_count = 0
        rows = db.execute(
            "SELECT COUNT(*) as cnt FROM memory_fragments WHERE agent_id = ? AND scope = 'private'",
            (agent_id,)
        )
        if rows:
            shared_count = dict(rows[0])["cnt"]
        db.execute(
            "UPDATE memory_fragments SET scope = 'shared' WHERE agent_id = ? AND scope = 'private'",
            (agent_id,)
        )
        db.execute('DELETE FROM agents WHERE id = ?', (agent_id,))

        logger.info(f"✓ 删除 Agent {agent_id}: {shared_count} 条 private 记忆转为 shared")
        return {
            "success": True,
            "agent_id": agent_id,
            "private_memories_shared": shared_count,
            "message": f"Agent {agent_id} deleted, {shared_count} private memories converted to shared",
        }
    except Exception as e:
        logger.error(f"✗ 删除 Agent 失败: {e}")
        return {"success": False, "error": str(e)}


def validate_agent_in_workspace(agent_id: int,
                                user_id: int,
                                workspace_id: Optional[int] = None) -> bool:
    """校验 Agent 属于指定 workspace（供召回侧复用）"""
    result = get_agent(agent_id, user_id, workspace_id)
    return bool(result.get("success"))
