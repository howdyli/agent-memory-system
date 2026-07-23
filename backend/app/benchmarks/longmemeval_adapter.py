"""
LongMemEval 数据集适配器

加载 LongMemEval 格式的数据集，将每个实例的 haystack_sessions 喂给
AgentMemoryClient 进行记忆存储，然后基于召回的记忆生成答案。

数据集格式（每条实例）:
    {
        "question_id": "e47becba",
        "question_type": "single-session-user",
        "question": "What degree did I graduate with?",
        "answer": "Business Administration",
        "question_date": "2023/05/30 (Tue) 23:40",
        "haystack_session_ids": ["session_1", "session_2", ...],
        "haystack_dates": ["2023/01/01", "2023/01/15", ...],
        "haystack_sessions": [
            [{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}],
            ...
        ],
        "answer_session_ids": ["answer_xxx"]
    }

5 种核心记忆能力 → question_type 映射:
    1. 信息提取      → single-session-user / single-session-preference
    2. 多会话推理    → multi-session
    3. 时间推理      → temporal-reasoning
    4. 知识更新      → knowledge-update
    5. 弃权          → question_id 以 _abs 结尾
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ============================================================
# 能力类别映射
# ============================================================

QUESTION_TYPE_TO_ABILITY = {
    "single-session-user": "information_extraction",
    "single-session-preference": "information_extraction",
    "multi-session": "multi_session_reasoning",
    "temporal-reasoning": "temporal_reasoning",
    "knowledge-update": "knowledge_update",
    "abstention": "abstention",
}

ABILITY_LABELS = {
    "information_extraction": "信息提取",
    "multi_session_reasoning": "多会话推理",
    "temporal_reasoning": "时间推理",
    "knowledge_update": "知识更新",
    "abstention": "弃权",
}


def get_ability(instance: Dict[str, Any]) -> str:
    """从实例中提取记忆能力类别。

    弃权问题（question_id 以 _abs 结尾）优先识别。
    """
    qid = instance.get("question_id", "")
    if qid.endswith("_abs"):
        return "abstention"
    qt = instance.get("question_type", "")
    return QUESTION_TYPE_TO_ABILITY.get(qt, "unknown")


# ============================================================
# 数据集加载
# ============================================================

def load_dataset(filepath: str) -> List[Dict[str, Any]]:
    """加载 LongMemEval JSON 数据集文件。

    Args:
        filepath: JSON 文件路径（longmemeval_s.json / _m.json / _oracle.json）

    Returns:
        实例列表（每条包含 question/answer/haystack_sessions 等字段）
    """
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError(f"数据集格式错误：期望列表，得到 {type(data).__name__}")
    logger.info(f"加载 LongMemEval 数据集: {filepath}，共 {len(data)} 条实例")
    return data


# ============================================================
# 会话文本化
# ============================================================

def session_to_text(session: List[Dict[str, Any]], include_assistant: bool = True) -> str:
    """将一个会话（turn 列表）转为纯文本。

    Args:
        session: [{"role": "user", "content": "..."}, ...]
        include_assistant: 是否包含 assistant 回复（记忆存储时通常只存用户信息）

    Returns:
        格式化的会话文本
    """
    lines = []
    for turn in session:
        role = turn.get("role", "user")
        content = turn.get("content", "")
        if role == "assistant" and not include_assistant:
            continue
        prefix = "用户" if role == "user" else "助手"
        lines.append(f"{prefix}: {content}")
    return "\n".join(lines)


def extract_user_facts(session: List[Dict[str, Any]]) -> str:
    """从会话中提取用户陈述的事实（仅 user turn 的 content）。

    LongMemEval 评估的是记忆系统对用户信息的记忆能力，
    因此存储记忆时主要关注用户说的话。
    """
    user_messages = []
    for turn in session:
        if turn.get("role") == "user":
            user_messages.append(turn.get("content", ""))
    return "\n".join(user_messages)


# ============================================================
# 记忆存储适配器
# ============================================================

class MemoryAdapter:
    """将 LongMemEval 会话喂给 AgentMemoryClient。

    策略：
    1. 每个会话的用户消息作为记忆片段存储（fragment_type=info）
    2. 同时提取关键事实存为 KV 变量（便于精确召回）
    3. 会话按时间顺序处理，模拟真实对话流
    4. P0-3: 存储新记忆时检测知识更新，标记旧记忆为 "superseded"
    """

    def __init__(self, user_id: int, workspace_id: Optional[int] = None):
        from app.services.agent_memory_sdk import AgentMemoryClient
        self.sdk = AgentMemoryClient(user_id, workspace_id)
        self.user_id = user_id
        self.workspace_id = workspace_id
        self.stored_count = 0

    def ingest_session(
        self,
        session: List[Dict[str, Any]],
        session_id: str = "",
        session_date: str = "",
    ) -> int:
        """将一个会话的用户消息存入记忆系统。

        P1-1: 使用会话分解索引，将长消息拆分为原子事实独立存储，
              提升细粒度信息的可检索性。
        P0-3: 存储每条新记忆后，检测是否与已有记忆构成知识更新，
              若是则将旧记忆标记为 "superseded"。
        P1-2: 为每条记忆生成多键索引（事实键/语义键/时间键）。

        Args:
            session: turn 列表
            session_id: 会话 ID（用于日志）
            session_date: 会话日期（附加到记忆内容中）

        Returns:
            存储的记忆条数
        """
        # P1-1: 使用会话分解索引替代原始整条消息存储
        try:
            from app.services.advanced_recall import ingest_session_decomposed
            result = ingest_session_decomposed(
                user_id=self.user_id,
                session=session,
                session_id=session_id,
                session_date=session_date,
                workspace_id=self.workspace_id,
            )
            if result.get("success"):
                count = result.get("stored", 0)
                self.stored_count += count
                return count
        except Exception as e:
            logger.debug(f"P1 会话分解索引失败，降级到原始存储: {e}")

        # 降级到原始整条消息存储
        count = 0
        for turn in session:
            if turn.get("role") != "user":
                continue
            content = turn.get("content", "").strip()
            if not content:
                continue

            # 构造带时间戳的记忆内容
            memory_content = content
            if session_date:
                memory_content = f"[{session_date}] {content}"

            try:
                self.sdk.remember_fragment(
                    content=memory_content,
                    fragment_type="info",
                    importance_score=0.6,
                )
                count += 1
                self.stored_count += 1

                # P0-3: 知识更新检测
                try:
                    from app.services.advanced_recall import detect_knowledge_update
                    detect_knowledge_update(
                        user_id=self.user_id,
                        new_content=memory_content,
                        workspace_id=self.workspace_id,
                    )
                except Exception as e:
                    logger.debug(f"知识更新检测失败: {e}")

            except Exception as e:
                logger.debug(f"存储记忆失败 (session={session_id}): {e}")

        return count

    def ingest_history(
        self,
        haystack_sessions: List[List[Dict[str, Any]]],
        haystack_dates: Optional[List[str]] = None,
        haystack_ids: Optional[List[str]] = None,
    ) -> int:
        """按时间顺序将所有会话存入记忆系统。

        Args:
            haystack_sessions: 会话列表
            haystack_dates: 每个会话的日期
            haystack_ids: 每个会话的 ID

        Returns:
            存储的总记忆条数
        """
        total = 0
        for i, session in enumerate(haystack_sessions):
            date = haystack_dates[i] if haystack_dates and i < len(haystack_dates) else ""
            sid = haystack_ids[i] if haystack_ids and i < len(haystack_ids) else f"session_{i}"
            total += self.ingest_session(session, sid, date)
        logger.info(f"已摄入 {len(haystack_sessions)} 个会话，共 {total} 条记忆")
        return total

    def recall_for_question(self, question: str, top_k: int = 10) -> str:
        """针对问题召回相关记忆。

        P1 优化：使用增强版高级召回策略（P0+P1），自动根据问题类型选择：
        - 时间推理问题 → P1-3 时间感知查询扩展召回
        - 多会话推理问题 → P0-1 多会话聚合召回
        - 其他问题 → 标准召回 + 过滤已过时记忆（P0-3）
        所有策略叠加 P1-2 多键索引融合。

        Args:
            question: 用户问题
            top_k: 召回条数

        Returns:
            召回的记忆上下文文本
        """
        try:
            from app.services.advanced_recall import advanced_recall_v2
            result = advanced_recall_v2(
                user_id=self.user_id,
                question=question,
                top_k=top_k,
                workspace_id=self.workspace_id,
            )
            if result.get("success") and result.get("context", "").strip():
                return result.get("context", "")
            logger.debug(f"高级召回v2返回空上下文，降级到 P0")
        except Exception as e:
            logger.debug(f"高级召回v2异常，降级到 P0: {e}")

        # 降级到 P0 高级召回
        try:
            from app.services.advanced_recall import advanced_recall
            result = advanced_recall(
                user_id=self.user_id,
                question=question,
                top_k=top_k,
                workspace_id=self.workspace_id,
            )
            if result.get("success") and result.get("context", "").strip():
                return result.get("context", "")
        except Exception as e:
            logger.debug(f"P0 高级召回异常，降级到标准召回: {e}")

        # 最终降级到标准召回
        return self.sdk.recall(query=question, top_k=top_k)

    def reset(self) -> None:
        """重置记忆（清理本用户的测试数据）。"""
        from app.core.db_client import get_db_client
        db = get_db_client()
        try:
            db.execute("DELETE FROM memory_fragments WHERE user_id = ?", (self.user_id,))
            db.execute("DELETE FROM memory_variables WHERE user_id = ?", (self.user_id,))
            # P1-2: 清理多键索引表
            try:
                db.execute("DELETE FROM memory_search_keys WHERE user_id = ?", (self.user_id,))
            except Exception:
                pass
        except Exception as e:
            logger.debug(f"清理记忆失败: {e}")


# ============================================================
# 答案生成
# ============================================================

def generate_answer(
    question: str,
    recalled_context: str,
    user_id: int,
    question_date: str = "",
) -> str:
    """基于召回的记忆上下文生成答案。

    使用 LLM 阅读召回的记忆并回答问题。如果 LLM 不可用，则使用
    简单的文本匹配回退策略。

    Args:
        question: 问题
        recalled_context: 召回的记忆上下文
        user_id: 用户 ID（用于 LLM 调用）
        question_date: 问题日期

    Returns:
        生成的答案
    """
    # 如果没有召回任何记忆，返回弃权答案
    if not recalled_context or not recalled_context.strip():
        return "I don't have enough information to answer this question."

    # 尝试使用 LLM 生成答案
    try:
        from app.services.llm_backend_service import llm_chat

        date_hint = f"\nCurrent Date: {question_date}" if question_date else ""
        prompt = f"""Based on the following memory context, answer the question.

Memory Context:
{recalled_context[:4000]}
{date_hint}

Question: {question}

Answer (be concise and factual, based only on the memory context above):
"""

        result = llm_chat(
            user_id=user_id,
            messages=[
                {"role": "system", "content": "You are a helpful assistant that answers questions based on stored memories. Be concise and accurate."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            enqueue_on_failure=False,
        )

        if result.get("success") and result.get("content"):
            return result["content"].strip()
    except Exception as e:
        logger.debug(f"LLM 答案生成失败: {e}")

    # 回退策略：从召回上下文中提取最相关的片段
    return _heuristic_answer(question, recalled_context)


def _heuristic_answer(question: str, context: str) -> str:
    """启发式答案生成：从上下文中提取与问题最相关的句子。

    当 LLM 不可用时的降级策略。
    """
    import re

    # 提取问题中的关键词
    question_words = set(question.lower().split())
    # 移除常见停用词
    stop_words = {"what", "where", "when", "who", "how", "why", "did", "do", "is", "are", "the", "a", "an", "i", "my", "me", "you", "to", "of", "in", "on", "at"}
    keywords = question_words - stop_words

    # 将上下文分句，按关键词匹配度排序
    sentences = []
    for line in context.split("\n"):
        # 跳过 P0 优化输出的格式标签行（如 [相关记忆]、[时间感知记忆...]、[多会话聚合记忆]）
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]") and len(stripped) < 60:
            continue
        for sent in line.split("。"):
            sent = sent.strip()
            if not sent or len(sent) < 5:
                continue
            score = sum(1 for kw in keywords if kw in sent.lower())
            sentences.append((score, sent))

    if not sentences:
        return context[:200] if context else "No relevant information found."

    # 取最相关的句子
    sentences.sort(key=lambda x: x[0], reverse=True)
    top = sentences[0][1]
    return top[:500]


# ============================================================
# 数据集统计
# ============================================================

def dataset_stats(instances: List[Dict[str, Any]]) -> Dict[str, Any]:
    """计算数据集统计信息。"""
    stats: Dict[str, Any] = {
        "total": len(instances),
        "by_ability": {},
        "by_question_type": {},
        "total_sessions": 0,
        "total_turns": 0,
        "total_user_turns": 0,
    }

    for inst in instances:
        ability = get_ability(inst)
        stats["by_ability"][ability] = stats["by_ability"].get(ability, 0) + 1

        qt = inst.get("question_type", "unknown")
        stats["by_question_type"][qt] = stats["by_question_type"].get(qt, 0) + 1

        sessions = inst.get("haystack_sessions", [])
        stats["total_sessions"] += len(sessions)
        for session in sessions:
            stats["total_turns"] += len(session)
            for turn in session:
                if turn.get("role") == "user":
                    stats["total_user_turns"] += 1

    stats["avg_sessions_per_instance"] = (
        stats["total_sessions"] / stats["total"] if stats["total"] else 0
    )
    stats["avg_user_turns_per_instance"] = (
        stats["total_user_turns"] / stats["total"] if stats["total"] else 0
    )

    return stats
