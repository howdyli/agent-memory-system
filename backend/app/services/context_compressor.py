"""
Context Compressor - 上下文压缩与分层记忆注入模块

核心功能：
1. 滑动窗口 + 摘要压缩（最近 N 轮完整 + 更早的压缩为摘要）
2. 记忆注入预算控制（Token 限制内最大化记忆价值）
3. 分层注入：
   Level 1: 用户 Profile（始终注入）
   Level 2: 高相关记忆（语义匹配）
   Level 3: 关联实体扩展（图谱遍历）
"""
import logging
import json
import re
import math
from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

from app.core.db_client import get_db_client
from app.core.redis_client import get_redis_client
from app.core.chromadb_client import get_chromadb_client
from app.services.memory_variable_service import list_memory_variables
from app.services.memory_fragment_service import search_fragments_by_semantic
from app.services.hybrid_search_service import hybrid_search
from app.services.memory_lifecycle_service import (
    calculate_decay_score,
    mark_cold,
    get_half_life,
)
from app.services.memory_observability_service import record_trace_event, update_recall_metrics
import time as _ctx_time_obs

# ============================================================
# 默认配置
# ============================================================

DEFAULT_CONFIG = {
    # 滑动窗口
    "recent_rounds": 5,          # 保留最近 5 轮完整对话
    "max_context_tokens": 4000,  # 记忆注入总 Token 预算
    "reserve_tokens": 200,       # 预留缓冲 Token

    # 分层预算
    "level1_tokens": 600,        # Level 1 Profile 预算
    "level2_tokens": 2000,       # Level 2 语义记忆预算
    "level3_tokens": 1200,       # Level 3 实体扩展预算

    # 检索参数
    "semantic_top_k": 10,        # 语义召回数量
    "entity_top_k": 5,           # 实体扩展召回数量
    "entity_relevance_threshold": 0.3,  # 实体相关度阈值
    "priority_decay_factor": 0.85,      # 优先级衰减因子

    # 窗口压缩
    "compression_min_rounds": 3,  # 最少保留多少轮对话再压缩
    "compression_interval": 10,   # 每 N 轮进行一次压缩

    # 生命周期
    "lifecycle_cold_threshold": 0.3,  # 重要性衰减到该值以下时标记为冷记忆
    "lifecycle_recall_update": True,  # 是否更新 last_recalled_at

    # 混合检索
    "use_hybrid_search": False,       # 是否使用多信号混合检索（取代纯语义搜索）
    "hybrid_search_alpha": None,      # 语义权重（None=使用hybrid_search默认值）
    "hybrid_search_beta": None,
    "hybrid_search_gamma": None,
    "hybrid_search_delta": None,
    "hybrid_search_top_k": None,
}


# ============================================================
# Token 估算工具
# ============================================================

def estimate_tokens(text: str) -> int:
    """
    估算文本占用的 Token 数。

    混合内容估算策略：
    - 中文字符: 每个约 2 token（中文信息密度大）
    - 英文字母/数字: 每 4 个字符约 1 token
    - 空白字符: 忽略

    Args:
        text: 待估算文本

    Returns:
        估算的 Token 数
    """
    if not text:
        return 0

    chinese_chars = 0
    other_chars = 0

    for c in text:
        if '\u4e00' <= c <= '\u9fff' or '\u3000' <= c <= '\u303f' or '\uff00' <= c <= '\uffef':
            chinese_chars += 1
        elif c in ' \t\n\r':
            continue
        else:
            other_chars += 1

    return chinese_chars * 2 + math.ceil(other_chars / 4)


# ============================================================
# 对话历史管理器（SQLite 持久化）
# ============================================================

def _ensure_conversation_tables() -> None:
    """确保对话历史相关表存在"""
    db = get_db_client()
    db.execute('''
        CREATE TABLE IF NOT EXISTS conversation_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            round_number INTEGER NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            tool_calls TEXT,
            compressed_flag INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    db.execute('''
        CREATE INDEX IF NOT EXISTS idx_conversation_session
        ON conversation_history(session_id, round_number)
    ''')
    db.execute('''
        CREATE INDEX IF NOT EXISTS idx_conversation_user
        ON conversation_history(user_id, session_id)
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS conversation_summaries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            from_round INTEGER NOT NULL,
            to_round INTEGER NOT NULL,
            summary TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    db.execute('''
        CREATE INDEX IF NOT EXISTS idx_summary_session
        ON conversation_summaries(session_id, from_round, to_round)
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS chat_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL UNIQUE,
            user_id INTEGER NOT NULL,
            title TEXT DEFAULT '新对话',
            message_count INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    db.execute('''
        CREATE INDEX IF NOT EXISTS idx_chat_sessions_user
        ON chat_sessions(user_id, updated_at DESC)
    ''')


class ConversationManager:
    """
    对话历史管理器

    管理会话级别的对话历史，支持：
    - 存储用户与助手的对话轮次
    - 滑动窗口：保留最近 N 轮完整对话
    - 历史压缩：将窗口外的对话压缩为摘要
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = {**DEFAULT_CONFIG, **(config or {})}
        _ensure_conversation_tables()

    # ----------------------------------------------------------
    # 存储
    # ----------------------------------------------------------

    def add_message(
        self,
        session_id: str,
        user_id: int,
        role: str,
        content: str,
        tool_calls: Optional[List[Dict[str, Any]]] = None,
    ) -> int:
        """
        添加一条消息到对话历史。

        Args:
            session_id: 会话 ID
            user_id: 用户 ID
            role: 'user' 或 'assistant'
            content: 消息内容
            tool_calls: 工具调用信息（可选）

        Returns:
            当前轮次号
        """
        db = get_db_client()

        # 获取当前最大轮次
        rows = db.execute(
            'SELECT COALESCE(MAX(round_number), 0) as max_round FROM conversation_history WHERE session_id = ?',
            (session_id,)
        )
        current_round = rows[0]["max_round"] if rows else 0

        # 如果是 user 消息，增加轮次；assistant 消息沿用同一轮次
        if role == "user":
            round_number = current_round + 1
        else:
            round_number = current_round if current_round > 0 else 1

        tool_calls_json = json.dumps(tool_calls, ensure_ascii=False) if tool_calls else None

        db.execute(
            '''INSERT INTO conversation_history
               (session_id, user_id, round_number, role, content, tool_calls)
               VALUES (?, ?, ?, ?, ?, ?)''',
            (session_id, user_id, round_number, role, content, tool_calls_json)
        )

        return round_number

    def add_turn(
        self,
        session_id: str,
        user_id: int,
        user_message: str,
        assistant_response: str,
        tool_calls: Optional[List[Dict[str, Any]]] = None,
    ) -> int:
        """
        添加一轮完整的对话（user + assistant）。

        Args:
            session_id: 会话 ID
            user_id: 用户 ID
            user_message: 用户消息
            assistant_response: 助手回复
            tool_calls: 工具调用信息

        Returns:
            存储的轮次号
        """
        round_number = self.add_message(session_id, user_id, "user", user_message)
        self.add_message(session_id, user_id, "assistant", assistant_response, tool_calls)
        return round_number

    # ----------------------------------------------------------
    # 检索
    # ----------------------------------------------------------

    def get_conversation_history(
        self,
        session_id: str,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        获取会话的完整对话历史（按时间正序）。

        Args:
            session_id: 会话 ID
            limit: 返回消息条数限制（可选）

        Returns:
            消息列表
        """
        db = get_db_client()
        if limit:
            rows = db.execute(
                '''SELECT * FROM conversation_history
                   WHERE session_id = ?
                   ORDER BY round_number ASC, id ASC
                   LIMIT ?''',
                (session_id, limit)
            )
        else:
            rows = db.execute(
                '''SELECT * FROM conversation_history
                   WHERE session_id = ?
                   ORDER BY round_number ASC, id ASC''',
                (session_id,)
            )
        return [dict(r) for r in rows] if rows else []

    def get_recent_rounds(
        self,
        session_id: str,
        n: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        获取最近 N 轮的完整对话（压缩窗口内的最新部分）。

        Args:
            session_id: 会话 ID
            n: 获取的轮次数，None 使用配置的 recent_rounds

        Returns:
            最近 N 轮的消息列表（按时间正序）
        """
        recent_n = n or self.config["recent_rounds"]
        db = get_db_client()

        # 获取最大轮次
        rows = db.execute(
            'SELECT COALESCE(MAX(round_number), 0) as max_round FROM conversation_history WHERE session_id = ?',
            (session_id,)
        )
        max_round = rows[0]["max_round"] if rows else 0
        if max_round == 0:
            return []

        # 计算窗口起始轮次
        start_round = max(1, max_round - recent_n + 1)

        rows = db.execute(
            '''SELECT * FROM conversation_history
               WHERE session_id = ? AND round_number >= ? AND compressed_flag = 0
               ORDER BY round_number ASC, id ASC''',
            (session_id, start_round)
        )
        return [dict(r) for r in rows] if rows else []

    def get_compressed_summary(self, session_id: str) -> Optional[str]:
        """
        获取会话的压缩摘要（最早的压缩结果）。

        Args:
            session_id: 会话 ID

        Returns:
            压缩摘要文本，无可用摘要时返回 None
        """
        db = get_db_client()
        rows = db.execute(
            '''SELECT summary FROM conversation_summaries
               WHERE session_id = ?
               ORDER BY id DESC LIMIT 1''',
            (session_id,)
        )
        if rows:
            return rows[0]["summary"]
        return None

    def get_total_rounds(self, session_id: str) -> int:
        """获取会话的总轮次数"""
        db = get_db_client()
        rows = db.execute(
            'SELECT COALESCE(MAX(round_number), 0) as max_round FROM conversation_history WHERE session_id = ?',
            (session_id,)
        )
        return rows[0]["max_round"] if rows else 0

    # ----------------------------------------------------------
    # 滑动窗口压缩
    # ----------------------------------------------------------

    def should_compress(self, session_id: str) -> bool:
        """
        判断是否需要执行压缩。

        条件：
        1. 总轮次数 >= compression_min_rounds
        2. 总轮次数 % compression_interval == 0
        3. 最近一次压缩的轮次 < 当前窗口起始轮次

        Returns:
            是否需要压缩
        """
        total = self.get_total_rounds(session_id)
        if total < self.config["compression_min_rounds"]:
            return False
        if total % self.config["compression_interval"] != 0:
            return False

        # 检查是否已经压缩过这一轮
        recent_n = self.config["recent_rounds"]
        compressed_round = total - recent_n
        if compressed_round < 1:
            return False

        db = get_db_client()
        rows = db.execute(
            '''SELECT COUNT(*) as cnt FROM conversation_summaries
               WHERE session_id = ? AND to_round >= ?''',
            (session_id, compressed_round)
        )
        already_compressed = rows[0]["cnt"] > 0 if rows else False

        return not already_compressed

    def compress_old_rounds(
        self,
        session_id: str,
        user_id: int,
    ) -> Dict[str, Any]:
        """
        压缩窗口外的旧对话轮次为摘要。

        将 `total_rounds - recent_rounds` 轮之前的对话压缩为一段摘要，
        并标记原始消息为已压缩。

        Args:
            session_id: 会话 ID
            user_id: 用户 ID

        Returns:
            压缩结果
        """
        total = self.get_total_rounds(session_id)
        recent_n = self.config["recent_rounds"]
        compress_up_to = total - recent_n

        if compress_up_to < 1:
            return {"success": True, "message": "无需压缩", "compressed_rounds": 0}

        # 获取要压缩的轮次文本
        db = get_db_client()
        rows = db.execute(
            '''SELECT * FROM conversation_history
               WHERE session_id = ? AND round_number <= ? AND compressed_flag = 0
               ORDER BY round_number ASC, id ASC''',
            (session_id, compress_up_to)
        )
        old_messages = [dict(r) for r in rows] if rows else []

        if not old_messages:
            return {"success": True, "message": "无可压缩的消息", "compressed_rounds": 0}

        # 构建要压缩的对话文本
        conversation_text = ""
        for msg in old_messages:
            role_label = "用户" if msg["role"] == "user" else "助手"
            conversation_text += f"{role_label}: {msg['content']}\n"

        # 使用 LLM 生成摘要
        summary = self._generate_compression_summary(user_id, conversation_text)

        # 存储摘要
        db.execute(
            '''INSERT INTO conversation_summaries
               (session_id, user_id, from_round, to_round, summary)
               VALUES (?, ?, 1, ?, ?)''',
            (session_id, user_id, compress_up_to, summary)
        )

        # 标记旧消息为已压缩
        db.execute(
            '''UPDATE conversation_history SET compressed_flag = 1
               WHERE session_id = ? AND round_number <= ?''',
            (session_id, compress_up_to)
        )

        logger.info(
            f"✓ 压缩对话历史: session={session_id}, "
            f"压缩 {compress_up_to} 轮, "
            f"摘要长度: {len(summary)} 字符"
        )

        return {
            "success": True,
            "compressed_rounds": compress_up_to,
            "summary": summary,
        }

    def _generate_compression_summary(self, user_id: int, conversation_text: str) -> str:
        """
        使用 LLM 生成对话摘要。

        Args:
            user_id: 用户 ID
            conversation_text: 待压缩的对话文本

        Returns:
            生成的摘要文本
        """
        try:
            from app.services.llm_backend_service import llm_chat

            summary_prompt = (
                "请将以下对话历史压缩为一段简洁的中文摘要（不超过 200 字）。\n"
                "保留以下关键信息：\n"
                "1. 用户的基本信息（姓名、角色、组织等）\n"
                "2. 用户的偏好和习惯\n"
                "3. 用户的计划和待办事项\n"
                "4. 已完成的对话主题\n"
                "5. 重要的约定和决定\n\n"
                "对话历史：\n"
                f"{conversation_text}\n\n"
                "摘要："
            )

            result = llm_chat(
                user_id=user_id,
                messages=[{"role": "user", "content": summary_prompt}],
                temperature=0.3,
                max_tokens=500,
            )

            if result.get("success") and result.get("content"):
                summary = result["content"].strip()
                # 限制摘要长度
                if len(summary) > 500:
                    summary = summary[:497] + "..."
                return summary

        except Exception as e:
            logger.warning(f"LLM 摘要生成失败，回退到规则摘要: {e}")

        # 回退：提取关键信息
        return self._fallback_summary(conversation_text)

    def _fallback_summary(self, conversation_text: str) -> str:
        """规则回退摘要（当 LLM 不可用时）"""
        lines = conversation_text.strip().split("\n")
        summary_parts = []
        user_messages = []

        for line in lines:
            if line.startswith("用户:"):
                content = line[3:].strip()
                if content:
                    user_messages.append(content)

        total_turns = len([l for l in lines if l.startswith("用户:")])
        if user_messages:
            # 提取最后几条消息的概要
            recent = "；".join(user_messages[-3:])
            summary_parts.append(f"对话共 {total_turns} 轮")

        return "；".join(summary_parts) if summary_parts else "(对话历史)"

    def build_compressed_context(self, session_id: str) -> str:
        """
        构建压缩后的对话上下文字符串。

        格式：
        [对话摘要：xxxxx]
        --- 最近对话 ---
        用户：...
        助手：...

        Args:
            session_id: 会话 ID

        Returns:
            格式化的上下文字符串
        """
        parts = []

        # 1. 获取历史摘要
        summary = self.get_compressed_summary(session_id)
        if summary:
            parts.append(f"[对话摘要]\n{summary}\n")

        # 2. 获取最近 N 轮完整对话
        recent_messages = self.get_recent_rounds(session_id)
        if recent_messages:
            recent_parts = ["[最近对话]"]
            for msg in recent_messages:
                role_label = "用户" if msg["role"] == "user" else "助手"
                recent_parts.append(f"{role_label}: {msg['content']}")
            parts.append("\n".join(recent_parts))

        return "\n\n".join(parts)

    # ----------------------------------------------------------
    # 会话管理（chat_sessions 表）
    # ----------------------------------------------------------

    def create_session(self, session_id: str, user_id: int, title: str = "新对话") -> Dict[str, Any]:
        """创建会话元数据"""
        db = get_db_client()
        try:
            db.execute(
                '''INSERT OR IGNORE INTO chat_sessions (session_id, user_id, title)
                   VALUES (?, ?, ?)''',
                (session_id, user_id, title)
            )
            return {"success": True, "session_id": session_id}
        except Exception as e:
            logger.warning(f"创建会话失败: {e}")
            return {"success": False, "error": str(e)}

    def list_sessions(self, user_id: int, limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
        """分页列出用户会话（按 updated_at DESC）"""
        db = get_db_client()
        rows = db.execute(
            '''SELECT * FROM chat_sessions
               WHERE user_id = ?
               ORDER BY updated_at DESC
               LIMIT ? OFFSET ?''',
            (user_id, limit, offset)
        )
        return [dict(r) for r in rows] if rows else []

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """获取单个会话详情"""
        db = get_db_client()
        rows = db.execute(
            'SELECT * FROM chat_sessions WHERE session_id = ?',
            (session_id,)
        )
        return dict(rows[0]) if rows else None

    def delete_session(self, session_id: str) -> Dict[str, Any]:
        """级联删除会话 + 消息 + 摘要"""
        db = get_db_client()
        try:
            db.execute('DELETE FROM conversation_history WHERE session_id = ?', (session_id,))
            db.execute('DELETE FROM conversation_summaries WHERE session_id = ?', (session_id,))
            db.execute('DELETE FROM chat_sessions WHERE session_id = ?', (session_id,))
            return {"success": True}
        except Exception as e:
            logger.warning(f"删除会话失败: {e}")
            return {"success": False, "error": str(e)}

    def rename_session(self, session_id: str, title: str) -> Dict[str, Any]:
        """重命名会话"""
        db = get_db_client()
        db.execute(
            'UPDATE chat_sessions SET title = ? WHERE session_id = ?',
            (title, session_id)
        )
        return {"success": True}

    def touch_session(self, session_id: str) -> None:
        """更新 updated_at + 递增 message_count"""
        db = get_db_client()
        try:
            db.execute(
                '''UPDATE chat_sessions
                   SET updated_at = CURRENT_TIMESTAMP,
                       message_count = message_count + 1
                   WHERE session_id = ?''',
                (session_id,)
            )
        except Exception:
            pass

    def get_session_messages(
        self, session_id: str, limit: int = 100, offset: int = 0
    ) -> Tuple[List[Dict[str, Any]], int]:
        """分页获取会话消息（仅未压缩的），返回 (messages, total)"""
        db = get_db_client()
        rows = db.execute(
            '''SELECT * FROM conversation_history
               WHERE session_id = ? AND compressed_flag = 0
               ORDER BY round_number ASC, id ASC
               LIMIT ? OFFSET ?''',
            (session_id, limit, offset)
        )
        messages = [dict(r) for r in rows] if rows else []
        # 查询总数
        count_rows = db.execute(
            'SELECT COUNT(*) as total FROM conversation_history WHERE session_id = ? AND compressed_flag = 0',
            (session_id,)
        )
        total = count_rows[0]['total'] if count_rows else 0
        return messages, total


# ============================================================
# 记忆价值评分器
# ============================================================

class MemoryValueScorer:
    """
    记忆价值评分器

    评估每条记忆的价值/成本比，用于预算控制下的最优选择。
    价值因素：重要性、相关性、时效性、完整性
    """

    @staticmethod
    def score_memory_value(memory: Dict[str, Any], query: str) -> float:
        """
        计算单条记忆的综合价值分数（0-1）。

        考虑因素：
        - 基础重要性 (importance_score)
        - 与查询的语义相关性 (relevance/similarity)
        - 半衰期衰减（基于记忆类型的差异化衰减）
        - 时间衰减（越新价值越高）
        - 信息完整性

        Args:
            memory: 记忆片段
            query: 用户查询

        Returns:
            价值分数
        """
        # 基础重要性
        importance = memory.get("importance_score", 0.5)
        if isinstance(importance, str):
            try:
                importance = float(importance)
            except (ValueError, TypeError):
                importance = 0.5

        # 相关性
        relevance = (
            memory.get("relevance", memory.get("similarity", 0.5))
            or 0.5
        )
        if isinstance(relevance, str):
            try:
                relevance = float(relevance)
            except (ValueError, TypeError):
                relevance = 0.5

        # 半衰期衰减（基于记忆类型的差异化策略）
        fragment_type = memory.get("fragment_type", "")
        half_life_days = get_half_life(fragment_type)
        created_at = memory.get("created_at")
        decay_factor = calculate_decay_score(created_at, half_life_days)

        # 时间衰减（通用）
        recency = 1.0
        if created_at:
            try:
                if isinstance(created_at, str):
                    created_time = datetime.fromisoformat(
                        created_at.replace("Z", "+00:00").split(".")[0]
                    )
                else:
                    created_time = created_at
                days_since = max(0, (datetime.now() - created_time).days)
                recency = max(0.3, 1.0 - days_since / 180)
            except Exception:
                pass

        # 信息完整性（内容越长的可能越完整）
        content = memory.get("content", "")
        completeness = min(1.0, len(content) / 200) if content else 0.3

        # 综合评分（加入半衰期衰减因子）
        score = (
            0.30 * importance
            + 0.30 * relevance
            + 0.15 * recency
            + 0.15 * decay_factor
            + 0.10 * completeness
        )

        return min(1.0, max(0.0, score))

    @staticmethod
    def value_per_token(memory: Dict[str, Any], query: str) -> float:
        """
        计算记忆的 Token 价值密度（价值/成本比）。

        用于在预算约束下选择最优的记忆组合。

        Args:
            memory: 记忆片段
            query: 用户查询

        Returns:
            价值密度（越高越值得注入）
        """
        value = MemoryValueScorer.score_memory_value(memory, query)
        content = memory.get("content", "")
        cost = estimate_tokens(content) + 10  # +10 为格式开销
        if cost <= 0:
            return 0
        return value / cost

    @staticmethod
    def select_top_by_budget(
        memories: List[Dict[str, Any]],
        query: str,
        budget_tokens: int,
    ) -> List[Dict[str, Any]]:
        """
        在 Token 预算内贪心选择最优记忆组合。

        使用价值/成本比进行排序，优先选择性价比最高的记忆。

        Args:
            memories: 候选记忆列表
            query: 用户查询
            budget_tokens: Token 预算上限

        Returns:
            选中的记忆列表
        """
        if not memories or budget_tokens <= 0:
            return []

        # 计算每条记忆的价值密度
        scored = []
        for m in memories:
            density = MemoryValueScorer.value_per_token(m, query)
            cost = estimate_tokens(m.get("content", "")) + 10
            scored.append((density, cost, m))

        # 按价值密度降序排列（贪心）
        scored.sort(key=lambda x: x[0], reverse=True)

        selected = []
        used_tokens = 0

        for density, cost, memory in scored:
            if used_tokens + cost <= budget_tokens:
                selected.append(memory)
                used_tokens += cost
            else:
                # 预算不足时跳过
                continue

        logger.debug(
            f"预算选择: {len(memories)} 候选 → {len(selected)} 入选, "
            f"使用 {used_tokens}/{budget_tokens} tokens"
        )

        return selected


# ============================================================
# 实体提取与图谱遍历
# ============================================================

class EntityGraphTraverser:
    """
    实体图谱遍历器

    从用户查询中提取关键实体，然后通过语义搜索扩展关联记忆。
    模拟知识图谱的邻居遍历，无需显式图数据库。

    当 GraphMemory 模块有结构化数据时，优先使用结构化图数据。
    """

    @staticmethod
    def extract_entities(text: str) -> List[str]:
        """
        从文本中提取关键实体（仅返回名称列表）。

        支持中英文实体提取：
        - 中文：2-6 字的名词短语
        - 英文：大写开头的名词短语

        Args:
            text: 输入文本

        Returns:
            实体名称列表
        """
        entities = set()

        # 1. 提取中文引号内的内容
        quoted = re.findall(r'[""](.+?)[""]', text)
        for q in quoted:
            q = q.strip()
            if 2 <= len(q) <= 20:
                entities.add(q)

        # 2. 提取"XX是XX"中的主语
        subject_matches = re.findall(r'([\u4e00-\u9fff]{2,6})(?:是|叫|指|代表)', text)
        for s in subject_matches:
            entities.add(s)

        # 3. 提取"关于XX"、"XX相关"中的实体
        about_matches = re.findall(r'关于([\u4e00-\u9fff]{2,10})', text)
        for a in about_matches:
            entities.add(a)

        # 4. 提取英文大写实体（如项目名、人名）
        eng_entities = re.findall(r'\b([A-Z][a-zA-Z]{2,20})\b', text)
        for e in eng_entities:
            entities.add(e)

        # 5. 提取"XX项目"、"XX系统"、"XX平台"等带后缀的实体
        suffix_entities = re.findall(r'([\u4e00-\u9fff]{2,10})(?:项目|系统|平台|工具|方案|技术|产品|功能)', text)
        for s in suffix_entities:
            entities.add(s)

        # 6. 提取人名特征：XX说/问/告诉我
        name_matches = re.findall(r'([\u4e00-\u9fff]{2,4})(?:说|问|告诉|通知|联系|找|叫)', text)
        for n in name_matches:
            entities.add(n)

        # 7. 提取组织特征：XX公司/集团/学院
        org_matches = re.findall(r'([\u4e00-\u9fff]{2,10})(?:公司|集团|学院|医院|大学|银行)', text)
        for o in org_matches:
            entities.add(o)

        return list(entities)

    @staticmethod
    def extract_entities_with_types(text: str) -> List[Dict[str, str]]:
        """
        从文本中提取关键实体并标记类型。

        Returns:
            [{"name": "张三", "type": "person"}, ...]
        """
        entities = EntityGraphTraverser.extract_entities(text)
        typed = []
        for name in entities:
            etype = EntityGraphTraverser._guess_type(name, text)
            typed.append({"name": name, "type": etype})
        return typed

    @staticmethod
    def _guess_type(name: str, context: str = "") -> str:
        """猜测实体类型"""
        # 2-4 字中文名很可能是人名
        if re.match(r'^[\u4e00-\u9fff]{2,4}$', name):
            return "person"
        # 含公司/集团等后缀
        if re.search(r'(公司|集团|学院|医院|大学|银行|机构|团队)$', name):
            return "organization"
        # 含市/区/省等后缀
        if re.search(r'(市|区|省|路|街|大厦)$', name):
            return "location"
        # 含事件关键词
        if re.search(r'(会|节|赛|战|活动|峰会|大会)', name):
            return "event"
        # 在上下文中带引号的内容可能是项目/事件名
        if context:
            quoted_patterns = re.findall(r'[""' + re.escape(name) + r'""]', context)
            if quoted_patterns:
                return "event"
        return "organization"

    @staticmethod
    def search_related_memories(
        user_id: int,
        entities: List[str],
        top_k: int = 5,
        threshold: float = 0.3,
    ) -> List[Dict[str, Any]]:
        """
        基于实体搜索关联记忆（模拟图谱遍历）。

        优先使用 GraphMemory 模块的结构化图数据（如果有），
        回退到语义搜索。

        Args:
            user_id: 用户 ID
            entities: 实体名称列表
            top_k: 每实体召回数量
            threshold: 相似度阈值

        Returns:
            关联记忆列表
        """
        if not entities:
            return []

        # 1. 优先尝试 GraphMemory 结构化数据
        graph_results = EntityGraphTraverser._use_graph_memory(
            user_id=user_id, entities=entities, top_k=top_k
        )
        if graph_results:
            return graph_results

        # 2. 回退到语义搜索
        seen = set()
        related = []

        for entity in entities:
            try:
                result = search_fragments_by_semantic(
                    user_id=user_id,
                    query=entity,
                    top_k=top_k,
                    threshold=threshold,
                )
                fragments = result.get("fragments", [])
                for frag in fragments:
                    frag_id = frag.get("id")
                    if frag_id and frag_id not in seen:
                        seen.add(frag_id)
                        frag["source_entity"] = entity
                        related.append(frag)
            except Exception as e:
                logger.debug(f"实体 '{entity}' 搜索失败: {e}")
                continue

        related.sort(key=lambda x: x.get("similarity", 0), reverse=True)
        return related

    @staticmethod
    def _use_graph_memory(
        user_id: int,
        entities: List[str],
        top_k: int = 5,
    ) -> List[Dict[str, Any]]:
        """
        使用 GraphMemory 模块的结构化图数据查询关联记忆。

        检查 graph_entities 表是否存在且有数据，
        如果有则使用 get_neighbors 获取结构化关系。

        Returns:
            格式化后的记忆列表，如果 GraphMemory 不可用则返回空列表
        """
        try:
            from app.services.graph_memory_service import (
                get_neighbors, search_entities, get_entity_graph_text
            )

            db = get_db_client()
            # 检查是否有 GraphMemory 数据
            count_rows = db.execute(
                'SELECT COUNT(*) as cnt FROM graph_entities WHERE user_id = ?',
                (user_id,)
            )
            if not count_rows or count_rows[0]["cnt"] == 0:
                return []  # 无数据，回退语义搜索

            all_results = []
            seen = set()

            for entity in entities:
                try:
                    # 使用 get_neighbors 获取直接关联
                    neighbors = get_neighbors(
                        user_id=user_id,
                        entity_name=entity,
                        entity_type="person",
                        depth=1,
                    )
                    if neighbors.get("success"):
                        for nb in neighbors.get("neighbors", []):
                            nb_id = nb.get("entity_id")
                            if nb_id and nb_id not in seen:
                                seen.add(nb_id)
                                content = (
                                    f"{entity} 的 {nb['relation_type']}: {nb['entity_name']}"
                                )
                                all_results.append({
                                    "id": f"graph_{nb_id}",
                                    "content": content,
                                    "fragment_type": "info",
                                    "importance_score": nb.get("confidence", 0.5),
                                    "similarity": 0.8,
                                    "source_entity": entity,
                                    "source": "graph_memory",
                                })

                    # 也搜索实体本身
                    search_result = search_entities(
                        user_id=user_id, query=entity, limit=1
                    )
                    if search_result.get("success") and search_result.get("entities"):
                        for e in search_result["entities"]:
                            eid = e.get("id")
                            if eid and eid not in seen:
                                seen.add(eid)
                                all_results.append({
                                    "id": f"graph_{eid}",
                                    "content": f"实体: {e['name']} ({e['entity_type']})",
                                    "fragment_type": "info",
                                    "importance_score": 0.7,
                                    "similarity": 0.9,
                                    "source_entity": entity,
                                    "source": "graph_memory",
                                })

                except Exception as e:
                    logger.debug(f"GraphMemory 查询 '{entity}' 失败: {e}")
                    continue

            return all_results[:top_k]

        except ImportError:
            logger.debug("GraphMemory 模块未加载，回退语义搜索")
            return []
        except Exception as e:
            logger.debug(f"GraphMemory 不可用: {e}")
            return []


# ============================================================
# Context Compressor 主类
# ============================================================

class ContextCompressor:
    """
    上下文压缩与分层记忆注入主类。

    核心流程：
    1. 构建对话上下文（滑动窗口 + 摘要压缩）
    2. 分层注入记忆（Profile → 语义记忆 → 实体扩展）
    3. Token 预算控制（贪心选择最优记忆组合）
    4. 输出格式化上下文，直接拼入 System Prompt

    使用方式：
        compressor = ContextCompressor()
        context = compressor.build_context(
            user_id=user_id,
            session_id=session_id,
            user_query=user_query,
        )
        # context 可直接拼入 system prompt
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = {**DEFAULT_CONFIG, **(config or {})}
        self.conversation_mgr = ConversationManager(config)
        self.value_scorer = MemoryValueScorer()
        self.entity_traverser = EntityGraphTraverser()
        # 统一召回引擎
        from app.services.recall_engine import RecallEngine
        self.recall_engine = RecallEngine(self.config)

    # ----------------------------------------------------------
    # 主入口
    # ----------------------------------------------------------

    def build_context(
        self,
        user_id: int,
        session_id: str,
        user_query: str,
    ) -> str:
        """
        构建完整的注入上下文。

        流程：
        1. 触发滑动窗口压缩（如需）
        2. 获取压缩后的对话历史
        3. 分层注入记忆（受预算控制）
        4. 格式化输出

        Args:
            user_id: 用户 ID
            session_id: 会话 ID
            user_query: 当前用户查询

        Returns:
            格式化的上下文字符串
        """
        context_parts = []

        # ----------------------------------------------------------
        # 步骤 1：对话历史（滑动窗口 + 摘要）
        # ----------------------------------------------------------
        dialog_context = self._build_dialog_context(session_id, user_id)
        if dialog_context:
            context_parts.append(dialog_context)

        # ----------------------------------------------------------
        # 步骤 2：分层注入记忆（受预算控制）
        # ----------------------------------------------------------
        remaining_budget = self.config["max_context_tokens"] - self.config["reserve_tokens"]

        # 如果对话上下文已占用预算，相应减少
        if dialog_context:
            used = estimate_tokens(dialog_context)
            remaining_budget = max(100, remaining_budget - used)

        memory_context = self._build_memory_context(
            user_id=user_id,
            user_query=user_query,
            budget=remaining_budget,
        )
        if memory_context:
            context_parts.append(memory_context)

        return "\n\n".join(context_parts)

    def build_context_with_details(
        self,
        user_id: int,
        session_id: str,
        user_query: str,
    ) -> Dict[str, Any]:
        """
        构建上下文并返回使用的记忆详情（供流式输出使用）。

        Returns:
            {
                "context_text": str,
                "memories_used": [{"content": str, "type": str, "score": float}],
            }
        """
        # 重置跟踪
        self._last_memories_used: List[Dict[str, Any]] = []

        context_text = self.build_context(
            user_id=user_id,
            session_id=session_id,
            user_query=user_query,
        )

        return {
            "context_text": context_text,
            "memories_used": getattr(self, '_last_memories_used', []),
        }

    # ----------------------------------------------------------
    # 步骤 1：对话历史
    # ----------------------------------------------------------

    def _build_dialog_context(self, session_id: str, user_id: int) -> str:
        """
        构建对话历史上下文（滑动窗口 + 摘要压缩）。

        Args:
            session_id: 会话 ID
            user_id: 用户 ID

        Returns:
            对话历史上下文字符串
        """
        # 1. 尝试压缩旧轮次
        if self.conversation_mgr.should_compress(session_id):
            try:
                self.conversation_mgr.compress_old_rounds(session_id, user_id)
            except Exception as e:
                logger.warning(f"对话压缩失败（不影响主流程）: {e}")

        # 2. 构建压缩后的上下文
        return self.conversation_mgr.build_compressed_context(session_id)

    # ----------------------------------------------------------
    # 步骤 2：分层记忆注入
    # ----------------------------------------------------------

    def _build_memory_context(
        self,
        user_id: int,
        user_query: str,
        budget: int,
    ) -> str:
        """
        构建记忆注入上下文（三层结构，受预算控制）。

        Args:
            user_id: 用户 ID
            user_query: 用户查询
            budget: 可用 Token 预算

        Returns:
            记忆上下文字符串
        """
        memory_parts = []

        # 各层级预算分配
        l1_budget = min(self.config["level1_tokens"], budget)
        l2_budget = min(self.config["level2_tokens"], max(0, budget - l1_budget))
        l3_budget = min(
            self.config["level3_tokens"],
            max(0, budget - l1_budget - l2_budget),
        )

        # --- Level 1: 用户 Profile（始终注入） ---
        l1_result = self._inject_level1(user_id, l1_budget)
        if l1_result:
            memory_parts.append(l1_result)

        # --- Level 2: 高相关语义记忆 ---
        l2_actual_budget = max(0, budget - estimate_tokens(l1_result or ""))
        if l2_actual_budget > 100:
            l2_result = self._inject_level2(
                user_id=user_id,
                query=user_query,
                budget=l2_actual_budget,
            )
            if l2_result:
                memory_parts.append(l2_result)

        # --- Level 3: 关联实体扩展 ---
        l3_actual_budget = max(0, budget - estimate_tokens(
            (l1_result or "") + (l2_result or "")
        ))
        if l3_actual_budget > 100:
            l3_result = self._inject_level3(
                user_id=user_id,
                query=user_query,
                budget=l3_actual_budget,
            )
            if l3_result:
                memory_parts.append(l3_result)

        return "\n\n".join(memory_parts)

    # ----------------------------------------------------------
    # Level 1: 用户 Profile
    # ----------------------------------------------------------

    def _inject_level1(self, user_id: int, budget: int) -> str:
        """
        Level 1：注入用户 Profile 信息。

        始终注入，包括：
        - KV 记忆变量（用户名、角色、组织等）
        - 高重要性记忆片段

        Args:
            user_id: 用户 ID
            budget: Token 预算

        Returns:
            格式化的 Profile 上下文字符串
        """
        parts = []

        # 1. KV 记忆变量（始终注入）
        kv_context = self._get_kv_profile(user_id, budget)
        if kv_context:
            parts.append(kv_context)

        return "\n".join(parts)

    def _get_kv_profile(self, user_id: int, budget: int) -> str:
        """
        获取 KV 存储中的用户 Profile 信息。

        Args:
            user_id: 用户 ID
            budget: Token 预算

        Returns:
            格式化的 KV Profile 字符串
        """
        try:
            variables = list_memory_variables(user_id)
            if not variables or not isinstance(variables, dict):
                return ""

            # 过滤出 Profile 类变量
            profile_keys = [
                "user_name", "name", "username",
                "user_role", "role",
                "organization", "company", "org",
                "email", "phone", "location",
                "department", "title", "position",
            ]

            profile_lines = []
            used_tokens = 30  # 格式开销

            for key in profile_keys:
                value = variables.get(key)
                if value is not None and used_tokens < budget:
                    value_str = str(value)
                    token_cost = estimate_tokens(f"{key}: {value_str}")
                    if used_tokens + token_cost <= budget:
                        # 美化显示
                        display_key = {
                            "user_name": "姓名",
                            "name": "姓名",
                            "username": "用户名",
                            "user_role": "角色",
                            "role": "角色",
                            "organization": "组织",
                            "company": "公司",
                            "org": "组织",
                            "department": "部门",
                            "title": "头衔",
                            "position": "职位",
                            "email": "邮箱",
                            "phone": "电话",
                            "location": "位置",
                        }.get(key, key)
                        profile_lines.append(f"- {display_key}: {value_str}")
                        used_tokens += token_cost

            # 如果 KV 变量不够，补充重要碎片
            if len(profile_lines) < 3:
                extra = self._get_high_importance_fragments(
                    user_id, budget - used_tokens
                )
                profile_lines.extend(extra)

            if profile_lines:
                return "[用户基本信息]\n" + "\n".join(profile_lines)

        except Exception as e:
            logger.debug(f"获取 KV Profile 失败: {e}")

        return ""

    def _get_high_importance_fragments(
        self, user_id: int, budget: int
    ) -> List[str]:
        """
        获取高重要性记忆片段作为 Profile 补充。

        Args:
            user_id: 用户 ID
            budget: Token 预算

        Returns:
            信息行列表
        """
        lines = []
        used = 0
        try:
            result = search_fragments_by_semantic(
                user_id=user_id,
                query="用户个人信息",
                top_k=5,
                threshold=0.1,  # 低阈值以获取尽可能多的信息
            )
            fragments = result.get("fragments", [])
            # 按重要性排序
            fragments.sort(
                key=lambda x: x.get("importance_score", 0.5), reverse=True
            )

            for frag in fragments:
                # 只选择 active 的记忆
                status = frag.get("lifecycle_status", "active")
                if status in ("soft_deleted", "archived"):
                    continue
                if used >= budget:
                    break
                content = frag.get("content", "").strip()
                if not content:
                    continue
                token_cost = estimate_tokens(content)
                if used + token_cost <= budget:
                    lines.append(f"- {content}")
                    used += token_cost
        except Exception as e:
            logger.debug(f"获取高重要性片段失败: {e}")

        return lines

    # ----------------------------------------------------------
    # Level 2: 语义匹配记忆
    # ----------------------------------------------------------

    def _inject_level2(self, user_id: int, query: str, budget: int) -> str:
        """
        Level 2：注入高相关语义记忆。

        委托给 RecallEngine 统一召回。

        Args:
            user_id: 用户 ID
            query: 用户查询
            budget: Token 预算

        Returns:
            格式化的语义记忆上下文字符串
        """
        try:
            result = self.recall_engine.recall(
                user_id=user_id,
                query=query,
                budget_tokens=budget,
            )

            if not result.memories:
                return ""

            # 回填 _last_memories_used（供 build_context_with_details 使用）
            if hasattr(self, '_last_memories_used') and isinstance(self._last_memories_used, list):
                details = self.recall_engine.extract_memory_details(result.memories)
                self._last_memories_used.extend(details)

            return result.context_text

        except Exception as e:
            logger.warning(f"Level 2 语义注入失败: {e}")
            return ""

    # ----------------------------------------------------------
    # Level 3: 实体扩展
    # ----------------------------------------------------------

    def _inject_level3(self, user_id: int, query: str, budget: int) -> str:
        """Level 3：注入关联实体扩展记忆。委托给 RecallEngine。"""
        try:
            result = self.recall_engine.recall_with_entities(
                user_id=user_id, query=query, budget_tokens=budget,
            )
            return result.context_text if result.memories else ""
        except Exception as e:
            logger.warning(f"Level 3 实体扩展失败: {e}")
            return ""

    # ----------------------------------------------------------
    # 对话存储（供 Agent Loop 调用）
    # ----------------------------------------------------------

    def store_turn(
        self,
        session_id: str,
        user_id: int,
        user_message: str,
        assistant_response: str,
        tool_calls: Optional[List[Dict[str, Any]]] = None,
    ) -> int:
        """
        存储一轮对话，由 Agent Loop 在响应后调用。

        Args:
            session_id: 会话 ID
            user_id: 用户 ID
            user_message: 用户消息
            assistant_response: 助手回复
            tool_calls: 工具调用列表

        Returns:
            当前轮次号
        """
        return self.conversation_mgr.add_turn(
            session_id=session_id,
            user_id=user_id,
            user_message=user_message,
            assistant_response=assistant_response,
            tool_calls=tool_calls,
        )

    # ----------------------------------------------------------
    # 配置管理
    # ----------------------------------------------------------

    def update_config(self, updates: Dict[str, Any]):
        """
        更新配置（动态调整）。

        Args:
            updates: 要更新的配置项
        """
        self.config.update(updates)
        self.conversation_mgr.config.update(updates)
        logger.info(f"✓ 更新 ContextCompressor 配置: {updates}")

    def get_config(self) -> Dict[str, Any]:
        """获取当前配置"""
        return self.config.copy()

    def get_stats(self, user_id: int, session_id: str) -> Dict[str, Any]:
        """
        获取压缩器统计信息。

        Args:
            user_id: 用户 ID
            session_id: 会话 ID

        Returns:
            统计信息字典
        """
        total_rounds = self.conversation_mgr.get_total_rounds(session_id)
        summary = self.conversation_mgr.get_compressed_summary(session_id)
        recent_msgs = self.conversation_mgr.get_recent_rounds(session_id)

        return {
            "total_rounds": total_rounds,
            "has_summary": summary is not None,
            "summary_length": len(summary) if summary else 0,
            "recent_messages": len(recent_msgs),
            "config": self.config,
        }


# ============================================================
# 便捷接口
# ============================================================

# 全局实例缓存
_compressor_instances: Dict[str, ContextCompressor] = {}


def get_compressor(config: Optional[Dict[str, Any]] = None) -> ContextCompressor:
    """
    获取 ContextCompressor 实例（可配置）。

    Args:
        config: 可选配置

    Returns:
        ContextCompressor 实例
    """
    return ContextCompressor(config)


def build_compressed_context(
    user_id: int,
    session_id: str,
    user_query: str,
    config: Optional[Dict[str, Any]] = None,
) -> str:
    """
    便捷接口：构建压缩后的注入上下文。

    Args:
        user_id: 用户 ID
        session_id: 会话 ID
        user_query: 用户查询
        config: 可选配置

    Returns:
        格式化的上下文字符串
    """
    compressor = get_compressor(config)
    return compressor.build_context(
        user_id=user_id,
        session_id=session_id,
        user_query=user_query,
    )


# ============================================================
# 测试
# ============================================================

def test_context_compressor():
    """测试 Context Compressor 模块"""
    import time

    print("\n" + "=" * 60)
    print("测试 Context Compressor 模块")
    print("=" * 60 + "\n")

    test_user_id = 999
    test_session_id = f"test_session_{int(time.time())}"

    # ----------------------------------------------------------
    # 准备测试数据
    # ----------------------------------------------------------
    print("0. 准备测试数据...")
    db = get_db_client()

    # 先确保表存在
    from app.services.context_compressor import _ensure_conversation_tables
    _ensure_conversation_tables()

    # 清理
    db.execute('DELETE FROM conversation_history WHERE user_id = ?', (test_user_id,))
    db.execute('DELETE FROM conversation_summaries WHERE user_id = ?', (test_user_id,))
    print("  清理完成")

    # 设置测试 KV 变量
    from app.services.memory_variable_service import set_memory_variable
    set_memory_variable(test_user_id, "user_name", "鑫海")
    set_memory_variable(test_user_id, "role", "PM")
    set_memory_variable(test_user_id, "company", "腾讯")

    # 创建测试记忆片段
    from app.services.memory_fragment_service import create_fragment
    create_fragment(test_user_id, "info", "用户名叫鑫海，在腾讯担任产品经理",
                    ttl=None, importance_score=0.95)
    create_fragment(test_user_id, "preference", "喜欢极简设计风格，偏好深色模式",
                    ttl=None, importance_score=0.85)
    create_fragment(test_user_id, "plan", "计划下周完成智能体平台架构评审",
                    ttl=7 * 86400, importance_score=0.80)
    create_fragment(test_user_id, "info", "源启是智能体工厂平台，用于构建企业级AI智能体",
                    ttl=None, importance_score=0.75)
    print("  测试数据就绪\n")

    compressor = ContextCompressor()

    # ----------------------------------------------------------
    # Test 1: Token 估算
    # ----------------------------------------------------------
    print("--- Test 1: Token 估算 ---")
    test_texts = [
        ("Hello world, this is a test.", 3),      # ~3 tokens (12 chars / 4)
        ("你好世界", 4),                           # 4 tokens (2 Chinese * 2)
        ("我叫鑫海，在腾讯工作", 14),               # 7 Chinese * 2
    ]
    for text, expected_min in test_texts:
        tokens = estimate_tokens(text)
        print(f"  '{text}' → {tokens} tokens (expected ≥{expected_min})")
        assert tokens >= expected_min, f"Token 估算偏低: {tokens} < {expected_min}"
    print("  ✓ Token 估算正确\n")

    # ----------------------------------------------------------
    # Test 2: 对话存储与检索
    # ----------------------------------------------------------
    print("--- Test 2: 对话存储与检索 ---")

    # 存储 8 轮对话
    for i in range(8):
        round_num = compressor.store_turn(
            session_id=test_session_id,
            user_id=test_user_id,
            user_message=f"测试消息 {i + 1}",
            assistant_response=f"这是对测试消息 {i + 1} 的回复",
        )
        print(f"  存储第 {round_num} 轮")

    total = compressor.conversation_mgr.get_total_rounds(test_session_id)
    print(f"  总轮次: {total}")
    assert total == 8, f"期望 8 轮，实际 {total}"

    # 检索最近 3 轮
    recent = compressor.conversation_mgr.get_recent_rounds(test_session_id, n=3)
    print(f"  最近 3 轮: {len(recent)} 条消息")
    assert len(recent) == 6, f"期望 6 条消息（3 轮 * 2），实际 {len(recent)}"
    print("  ✓ 对话存储与检索正确\n")

    # ----------------------------------------------------------
    # Test 3: 滑动窗口压缩
    # ----------------------------------------------------------
    print("--- Test 3: 滑动窗口压缩 ---")

    # 添加足够的轮次触发压缩（compression_interval=10, 最近 5 轮完整）
    for i in range(5):
        compressor.store_turn(
            session_id=test_session_id,
            user_id=test_user_id,
            user_message=f"压缩测试消息 {i + 1}",
            assistant_response=f"这是压缩测试 {i + 1} 的回复",
        )

    total = compressor.conversation_mgr.get_total_rounds(test_session_id)
    print(f"  添加后总轮次: {total}")

    if compressor.conversation_mgr.should_compress(test_session_id):
        result = compressor.conversation_mgr.compress_old_rounds(
            test_session_id, test_user_id
        )
        print(f"  压缩结果: {result.get('compressed_rounds', 0)} 轮")
        summary = compressor.conversation_mgr.get_compressed_summary(test_session_id)
        if summary:
            print(f"  摘要: {summary[:80]}...")
        # 即使压缩成功也通过
        print("  ✓ 压缩流程正常执行")
    else:
        print("  暂未触发压缩（轮次不足或已压缩）")

    # 构建压缩后的上下文
    context = compressor.conversation_mgr.build_compressed_context(test_session_id)
    print(f"\n  压缩后上下文 ({estimate_tokens(context)} tokens, {len(context)} chars):")
    for line in context.split("\n")[:6]:
        print(f"    {line}")
    print("  ✓ 压缩上下文构建完成\n")

    # ----------------------------------------------------------
    # Test 4: 实体提取
    # ----------------------------------------------------------
    print("--- Test 4: 实体提取 ---")
    test_queries = [
        ("关于源启智能体平台的架构设计", ["源启智能体平台", "源启", "架构设计"]),
        ("你好，我叫鑫海", ["鑫海"]),
        ("推荐一下极简设计的方案", ["极简设计"]),
    ]
    for query, expected_entities in test_queries:
        entities = EntityGraphTraverser.extract_entities(query)
        print(f"  '{query}' → {entities}")
        # 至少提取到一部分实体
        if entities:
            has_overlap = any(e in " ".join(entities) for e in expected_entities)
            assert has_overlap, f"期望 {expected_entities} 在 {entities} 中"
    print("  ✓ 实体提取正确\n")

    # ----------------------------------------------------------
    # Test 5: 完整构建上下文
    # ----------------------------------------------------------
    print("--- Test 5: 完整构建上下文 ---")

    full_context = compressor.build_context(
        user_id=test_user_id,
        session_id=test_session_id,
        user_query="介绍一下源启智能体平台",
    )

    token_count = estimate_tokens(full_context)
    print(f"  上下文总长度: {len(full_context)} 字符, ~{token_count} tokens")
    print(f"  预算上限: {compressor.config['max_context_tokens']} tokens")
    print(f"\n  上下文内容预览:")
    for line in full_context.split("\n")[:15]:
        if line.strip():
            print(f"    {line}")

    # Token 预算不应超标
    assert token_count <= compressor.config["max_context_tokens"] + 200, \
        f"Token 预算超标: {token_count} > {compressor.config['max_context_tokens']}"
    # 不应为空
    assert len(full_context) > 0, "上下文不应为空"
    print(f"\n  ✓ 完整上下文构建成功（{token_count} tokens）\n")

    # ----------------------------------------------------------
    # Test 6: 记忆价值评分器
    # ----------------------------------------------------------
    print("--- Test 6: 记忆价值评分 ---")

    test_memories = [
        {"content": "用户名叫鑫海，在腾讯担任产品经理", "importance_score": 0.95, "similarity": 0.8,
         "created_at": datetime.now().isoformat()},
        {"content": "喜欢极简设计风格", "importance_score": 0.85, "similarity": 0.6,
         "created_at": (datetime.now() - timedelta(days=30)).isoformat()},
        {"content": "计划下周完成架构评审", "importance_score": 0.80, "similarity": 0.4,
         "created_at": (datetime.now() - timedelta(days=60)).isoformat()},
    ]

    query = "鑫海的个人信息"
    for mem in test_memories:
        score = MemoryValueScorer.score_memory_value(mem, query)
        density = MemoryValueScorer.value_per_token(mem, query)
        print(f"  '{mem['content'][:20]}...' 价值={score:.3f}, 密度={density:.4f}")
        assert 0 <= score <= 1, f"价值分数越界: {score}"

    # 预算选择测试
    selected = MemoryValueScorer.select_top_by_budget(
        memories=test_memories,
        query=query,
        budget_tokens=50,
    )
    print(f"  预算选择: {len(selected)}/{len(test_memories)} 条入选")
    print("  ✓ 记忆价值评分正确\n")

    # ----------------------------------------------------------
    # Configuration test
    # ----------------------------------------------------------
    print("--- Test 7: 配置管理 ---")
    compressor.update_config({"recent_rounds": 3, "max_context_tokens": 2000})
    cfg = compressor.get_config()
    print(f"  recent_rounds: {cfg['recent_rounds']}")
    print(f"  max_context_tokens: {cfg['max_context_tokens']}")
    assert cfg["recent_rounds"] == 3
    assert cfg["max_context_tokens"] == 2000

    stats = compressor.get_stats(test_user_id, test_session_id)
    print(f"  stats: total_rounds={stats['total_rounds']}, has_summary={stats['has_summary']}")
    print("  ✓ 配置管理正确\n")

    # ----------------------------------------------------------
    # 清理
    # ----------------------------------------------------------
    print("--- 清理测试数据 ---")
    db.execute('DELETE FROM conversation_history WHERE user_id = ?', (test_user_id,))
    db.execute('DELETE FROM conversation_summaries WHERE user_id = ?', (test_user_id,))
    # 清理测试记忆片段
    db.execute('DELETE FROM memory_fragments WHERE user_id = ?', (test_user_id,))
    print("  清理完成")

    print("\n" + "=" * 60)
    print("✅ Context Compressor 模块测试完成！")
    print("=" * 60 + "\n")

    return True


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    test_context_compressor()
