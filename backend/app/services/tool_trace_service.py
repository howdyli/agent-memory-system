"""
工具调用轨迹服务（P2 R-14 程序记忆）

记录 agent_loop（memory_aware_chat）中每次工具调用的轨迹（工具名、参数、结果摘要、
成败、耗时），作为后续 LLM 提炼 procedure 记忆的数据源。
"""
import logging
import json
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)

from app.core.db_client import get_db_client

_RESULT_SUMMARY_MAX_LEN = 500


def _ensure_trace_table() -> None:
    """创建工具调用轨迹表（与 consolidation_runs 同款服务内建表模式）"""
    db = get_db_client()
    db.execute('''CREATE TABLE IF NOT EXISTS agent_tool_traces (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        session_id TEXT NOT NULL,
        round INTEGER DEFAULT 0,
        tool_name TEXT NOT NULL,
        arguments TEXT,
        result_summary TEXT,
        success INTEGER DEFAULT 1,
        duration_ms INTEGER,
        extracted INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id)
    )''')
    for sql in [
        'CREATE INDEX IF NOT EXISTS idx_tool_traces_user ON agent_tool_traces(user_id)',
        'CREATE INDEX IF NOT EXISTS idx_tool_traces_session ON agent_tool_traces(session_id)',
    ]:
        try:
            db.execute(sql)
        except Exception:
            pass


def _judge_success(result: Optional[str]) -> bool:
    """成败判定：result JSON 含 "success": false 视为失败，其余视为成功"""
    if not result:
        return False
    try:
        parsed = json.loads(result)
        if isinstance(parsed, dict) and parsed.get("success") is False:
            return False
    except (json.JSONDecodeError, TypeError):
        pass  # 非 JSON 结果视为成功（工具返回纯文本）
    return True


def record_tool_call(user_id: int,
                     session_id: str,
                     round_idx: int,
                     tool_name: str,
                     arguments: Optional[Dict[str, Any]],
                     result: Optional[str],
                     duration_ms: Optional[int] = None) -> Dict[str, Any]:
    """记录单次工具调用轨迹（异常不外抛，不影响主流程）"""
    try:
        _ensure_trace_table()
        db = get_db_client()
        result_summary = (result or "")[:_RESULT_SUMMARY_MAX_LEN]
        success = _judge_success(result)
        trace_id = db.execute(
            '''INSERT INTO agent_tool_traces
               (user_id, session_id, round, tool_name, arguments, result_summary, success, duration_ms)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
            (user_id, session_id, round_idx, tool_name,
             json.dumps(arguments or {}, ensure_ascii=False),
             result_summary, 1 if success else 0, duration_ms)
        )
        return {"success": True, "trace_id": trace_id}
    except Exception as e:
        logger.warning(f"⚠ 记录工具轨迹失败（不影响主流程）: {e}")
        return {"success": False, "error": str(e)}


def get_session_traces(session_id: str, user_id: Optional[int] = None) -> List[Dict[str, Any]]:
    """获取指定会话的全部工具调用轨迹（按时间正序）"""
    try:
        _ensure_trace_table()
        db = get_db_client()
        if user_id is not None:
            rows = db.execute(
                'SELECT * FROM agent_tool_traces WHERE session_id = ? AND user_id = ? ORDER BY id ASC',
                (session_id, user_id)
            )
        else:
            rows = db.execute(
                'SELECT * FROM agent_tool_traces WHERE session_id = ? ORDER BY id ASC',
                (session_id,)
            )
        return [dict(r) for r in rows] if rows else []
    except Exception as e:
        logger.warning(f"⚠ 查询工具轨迹失败: {e}")
        return []


def mark_session_extracted(session_id: str, user_id: int) -> None:
    """将会话轨迹标记为已提炼（避免维护周期反复扫描）"""
    try:
        db = get_db_client()
        db.execute(
            'UPDATE agent_tool_traces SET extracted = 1 WHERE session_id = ? AND user_id = ?',
            (session_id, user_id)
        )
    except Exception as e:
        logger.warning(f"⚠ 标记轨迹已提炼失败: {e}")
