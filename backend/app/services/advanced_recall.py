"""
高级召回服务 — P0 优化

三项 P0 改进，提升 LongMemEval 基准表现：

P0-1: 多会话信息聚合召回
    问题：多会话推理问题（如"我有多少年编程经验？"）需要跨会话聚合信息，
    单次召回可能遗漏部分会话的记忆。
    方案：将复杂问题分解为子查询，分别召回后合并去重，确保跨会话信息完整。

P0-2: 时间感知召回
    问题：时间推理问题（如"我最近在哪家公司工作？"）需要按时间顺序排列记忆，
    现有召回不考虑时间戳优先级。
    方案：从问题中提取时间线索，对时间范围内的记忆加权；按时间排序输出。

P0-3: 知识更新检测
    问题：用户更新信息后（如"我搬到旧金山了"），旧记忆（"我住纽约"）仍会被召回，
    导致矛盾答案。
    方案：存储新记忆时，检测与已有记忆的语义冲突，标记旧记忆为"已过时"，
    召回时优先返回最新记忆。
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

from app.core.db_client import get_db_client


# ============================================================
# P0-1: 多会话信息聚合召回
# ============================================================

# 多会话推理问题的特征词
_MULTI_SESSION_INDICATORS = [
    "how many", "how much", "total", "all", "both", "each",
    "every", "combined", "together", "in total", "altogether",
    "哪些", "所有", "总共", "一共", "每个", "哪些", "全部",
    "languages", "skills", "companies", "cities", "jobs",
]


def is_multi_session_question(question: str) -> bool:
    """判断问题是否需要多会话信息聚合。

    Args:
        question: 用户问题

    Returns:
        True 表示该问题需要跨会话聚合信息
    """
    q_lower = question.lower()
    for indicator in _MULTI_SESSION_INDICATORS:
        if indicator in q_lower:
            return True
    # 包含 "and" 连接的列举也可能需要多会话
    if " and " in q_lower and any(w in q_lower for w in ("what", "which", "list")):
        return True
    return False


def generate_sub_queries(question: str) -> List[str]:
    """将复杂问题分解为子查询。

    策略：
    1. 按连接词（and/comma）拆分
    2. 为 "how many/total" 类问题生成枚举子查询
    3. 原始问题始终作为最后一个子查询

    Args:
        question: 原始问题

    Returns:
        子查询列表（含原始问题）
    """
    sub_queries: List[str] = []

    # 策略 1：按 "and" 拆分
    if " and " in question.lower():
        parts = re.split(r'\s+and\s+', question, flags=re.IGNORECASE)
        for part in parts:
            part = part.strip().rstrip("?。.")
            if len(part) > 5:
                sub_queries.append(part)

    # 策略 2：按逗号拆分（中文/英文）
    if "," in question or "，" in question:
        parts = re.split(r'[,，]', question)
        for part in parts:
            part = part.strip().rstrip("?。.")
            if len(part) > 5:
                sub_queries.append(part)

    # 策略 3：为 "how many" 类问题生成通用召回词
    q_lower = question.lower()
    if "how many" in q_lower or "how much" in q_lower or "总共" in question:
        # 提取名词作为子查询
        nouns = _extract_key_nouns(question)
        sub_queries.extend(nouns)

    # 始终包含原始问题
    sub_queries.append(question)

    # 去重保序
    seen = set()
    unique = []
    for sq in sub_queries:
        if sq.lower() not in seen:
            seen.add(sq.lower())
            unique.append(sq)

    return unique if unique else [question]


def _extract_key_nouns(text: str) -> List[str]:
    """从文本中提取关键名词（简化版，无需 NLP 依赖）。

    移除常见疑问词和停用词，返回剩余的重要词。
    """
    stop_words = {
        "what", "where", "when", "who", "how", "why", "which",
        "many", "much", "total", "all", "the", "a", "an",
        "is", "are", "was", "were", "do", "did", "have", "has",
        "i", "my", "me", "you", "your", "in", "on", "at",
        "to", "of", "for", "with", "and", "or",
    }
    words = re.findall(r'\b[a-zA-Z]+\b', text.lower())
    keywords = [w for w in words if w not in stop_words and len(w) > 2]
    return keywords[:5]  # 最多 5 个关键词


def multi_session_recall(
    user_id: int,
    question: str,
    top_k: int = 10,
    workspace_id: Optional[int] = None,
) -> Dict[str, Any]:
    """多会话信息聚合召回。

    对多会话推理问题，将问题分解为子查询，分别召回后合并去重，
    确保跨会话信息被完整召回。

    Args:
        user_id: 用户 ID
        question: 问题
        top_k: 每个子查询召回的条数
        workspace_id: workspace ID

    Returns:
        {
            "success": bool,
            "memories": List[Dict],  # 合并去重后的记忆列表
            "context": str,          # 格式化的上下文文本
            "sub_queries": List[str], # 使用的子查询
            "source": "multi_session",
        }
    """
    try:
        from app.services.auto_recall_service import search_relevant_memories

        sub_queries = generate_sub_queries(question)
        all_memories: List[Dict[str, Any]] = []
        seen_ids = set()

        for sq in sub_queries:
            result = search_relevant_memories(
                user_id=user_id,
                query=sq,
                top_k=top_k,
                threshold=0.2,  # 多会话模式降低阈值以召回更多
                workspace_id=workspace_id,
            )
            if not result.get("success"):
                continue
            for mem in result.get("memories", []):
                mem_id = mem.get("id") or mem.get("content", "")[:50]
                if mem_id not in seen_ids:
                    seen_ids.add(mem_id)
                    # 标记来源子查询
                    mem["_sub_query"] = sq
                    all_memories.append(mem)

        # 按相关性和重要性排序
        all_memories.sort(
            key=lambda m: (
                m.get("similarity", m.get("relevance", 0)) * 0.6
                + m.get("importance_score", 0.5) * 0.4
            ),
            reverse=True,
        )

        # 限制总数
        all_memories = all_memories[: top_k * 2]

        # 格式化上下文
        context = _format_multi_session_context(all_memories, sub_queries)

        logger.info(
            f"✓ 多会话聚合召回: {len(sub_queries)} 子查询, "
            f"召回 {len(all_memories)} 条记忆"
        )

        return {
            "success": True,
            "memories": all_memories,
            "context": context,
            "sub_queries": sub_queries,
            "source": "multi_session",
            "memory_count": len(all_memories),
        }

    except Exception as e:
        logger.error(f"多会话聚合召回失败: {e}")
        return {
            "success": False,
            "error": str(e),
            "memories": [],
            "context": "",
            "sub_queries": [],
            "source": "multi_session",
        }


def _format_multi_session_context(
    memories: List[Dict[str, Any]],
    sub_queries: List[str],
) -> str:
    """格式化多会话召回的上下文。"""
    if not memories:
        return ""

    lines = ["[多会话聚合记忆]"]
    for i, mem in enumerate(memories, 1):
        content = mem.get("content", "")
        date = mem.get("created_at", "")
        date_str = f" ({date})" if date else ""
        source_sq = mem.get("_sub_query", "")
        source_str = f" [匹配: {source_sq[:30]}]" if source_sq else ""
        lines.append(f"{i}. {content}{date_str}{source_str}")

    return "\n".join(lines)


# ============================================================
# P0-2: 时间感知召回
# ============================================================

# 时间表达式模式（顺序敏感：before/after 必须在 year 之前匹配）
_TIME_PATTERNS = [
    # 之前/之后（必须在 year 之前匹配，否则 year 会先捕获）
    (r"before\s+(20\d{2})", "before"),
    (r"after\s+(20\d{2})", "after"),
    (r"在\s*(20\d{2})\s*之前", "before"),
    (r"在\s*(20\d{2})\s*之后", "after"),
    # 相对时间
    (r"(?:most\s+)?recently", "recent"),
    (r"last\s+(?:year|month|week|day)", "recent"),
    (r"最近|最新|上次|过去", "recent"),
    # 最早/最先
    (r"\bfirst\b|initially|originally|started|began", "earliest"),
    (r"最初|最早|开始|起初", "earliest"),
    # When 引导的时间问题
    (r"\bwhen\b", "temporal_when"),
    # 具体年份
    (r"\b(20\d{2})\b", "year"),
    # 时间顺序
    (r"chronological|timeline|sequence", "chronological"),
    (r"时间线|顺序|先后", "chronological"),
]


def extract_time_signals(question: str) -> Dict[str, Any]:
    """从问题中提取时间线索。

    Returns:
        {
            "has_time_signal": bool,
            "time_preference": "recent" | "earliest" | "chronological" | "before:YYYY" | "after:YYYY" | "year:YYYY" | None,
            "year": Optional[int],
            "raw_matches": List[str],
        }
    """
    q_lower = question.lower()
    matches = []
    preference = None
    year = None

    for pattern, signal_type in _TIME_PATTERNS:
        match = re.search(pattern, q_lower)
        if match:
            matches.append(match.group(0))
            if signal_type == "recent":
                preference = "recent"
            elif signal_type == "earliest":
                preference = "earliest"
            elif signal_type == "chronological":
                preference = "chronological"
            elif signal_type == "temporal_when":
                # "when" 引导的问题需要按时间排序
                preference = "chronological"
            elif signal_type in ("before", "after"):
                # 提取年份
                year_match = re.search(r"(20\d{2})", match.group(0))
                if year_match:
                    year = int(year_match.group(1))
                    preference = f"{signal_type}:{year}"
            elif signal_type == "year":
                year = int(match.group(1))
                preference = f"year:{year}"
            # 模式按优先级排序，设置 preference 后立即停止，
            # 防止低优先级模式（如 year）覆盖高优先级结果（如 before/after）
            break

    return {
        "has_time_signal": preference is not None,
        "time_preference": preference,
        "year": year,
        "raw_matches": matches,
    }


def is_temporal_question(question: str) -> bool:
    """判断问题是否为时间推理问题。"""
    return extract_time_signals(question)["has_time_signal"]


def time_aware_recall(
    user_id: int,
    question: str,
    top_k: int = 10,
    workspace_id: Optional[int] = None,
) -> Dict[str, Any]:
    """时间感知召回。

    对时间推理问题，按时间线索过滤和排序记忆。

    Args:
        user_id: 用户 ID
        question: 问题
        top_k: 召回条数
        workspace_id: workspace ID

    Returns:
        {
            "success": bool,
            "memories": List[Dict],
            "context": str,
            "time_signal": Dict,
            "source": "time_aware",
        }
    """
    try:
        from app.services.auto_recall_service import search_relevant_memories

        time_signal = extract_time_signals(question)
        preference = time_signal["time_preference"]

        # 先执行常规召回
        result = search_relevant_memories(
            user_id=user_id,
            query=question,
            top_k=top_k * 2,  # 多召回一些，过滤后再截断
            threshold=0.2,
            workspace_id=workspace_id,
        )

        if not result.get("success"):
            return {
                "success": False,
                "error": result.get("error", "recall failed"),
                "memories": [],
                "context": "",
                "time_signal": time_signal,
                "source": "time_aware",
            }

        memories = result.get("memories", [])

        # 解析记忆的时间戳
        for mem in memories:
            mem["_parsed_date"] = _parse_memory_date(mem)

        # 根据时间偏好过滤/排序
        if preference == "recent":
            # 最近的优先：按时间降序
            memories.sort(key=lambda m: m["_parsed_date"], reverse=True)
        elif preference == "earliest":
            # 最早的优先：按时间升序
            memories.sort(key=lambda m: m["_parsed_date"])
        elif preference == "chronological":
            # 按时间线排列：升序
            memories.sort(key=lambda m: m["_parsed_date"])
        elif preference and preference.startswith("before:"):
            # 某年之前
            year = int(preference.split(":")[1])
            cutoff = datetime(year, 1, 1)
            memories = [m for m in memories if m["_parsed_date"] < cutoff]
        elif preference and preference.startswith("after:"):
            # 某年之后
            year = int(preference.split(":")[1])
            cutoff = datetime(year, 1, 1)
            memories = [m for m in memories if m["_parsed_date"] >= cutoff]
        elif preference and preference.startswith("year:"):
            # 特定年份
            year = int(preference.split(":")[1])
            year_start = datetime(year, 1, 1)
            year_end = datetime(year + 1, 1, 1)
            # 优先返回该年的记忆，其余作为补充
            in_year = [m for m in memories if year_start <= m["_parsed_date"] < year_end]
            out_year = [m for m in memories if not (year_start <= m["_parsed_date"] < year_end)]
            memories = in_year + out_year

        # 截断到 top_k
        memories = memories[:top_k]

        # 格式化上下文（带时间标注）
        context = _format_temporal_context(memories, time_signal)

        logger.info(
            f"✓ 时间感知召回: preference={preference}, "
            f"召回 {len(memories)} 条记忆"
        )

        return {
            "success": True,
            "memories": memories,
            "context": context,
            "time_signal": time_signal,
            "source": "time_aware",
            "memory_count": len(memories),
        }

    except Exception as e:
        logger.error(f"时间感知召回失败: {e}")
        return {
            "success": False,
            "error": str(e),
            "memories": [],
            "context": "",
            "time_signal": {},
            "source": "time_aware",
        }


def _parse_memory_date(mem: Dict[str, Any]) -> datetime:
    """从记忆中解析时间戳，用于时间排序。"""
    # 尝试从 content 中的时间戳前缀解析（LongMemEval 格式: [2024/05/15] ...）
    content = mem.get("content", "")
    ts_match = re.match(r'\[(\d{4}[/-]\d{2}[/-]\d{2})\]', content)
    if ts_match:
        try:
            return datetime.strptime(ts_match.group(1).replace("-", "/"), "%Y/%m/%d")
        except ValueError:
            pass

    # 尝试从 created_at 字段解析
    created = mem.get("created_at")
    if created:
        try:
            if isinstance(created, str):
                # 处理各种 ISO 格式
                created = created.replace("Z", "+00:00")
                dt = datetime.fromisoformat(created)
                if dt.tzinfo:
                    dt = dt.replace(tzinfo=None)
                return dt
            elif isinstance(created, datetime):
                return created
        except (ValueError, TypeError):
            pass

    # 回退到很久以前（排到最后）
    return datetime(2000, 1, 1)


def _format_temporal_context(
    memories: List[Dict[str, Any]],
    time_signal: Dict[str, Any],
) -> str:
    """格式化时间感知召回的上下文。"""
    if not memories:
        return ""

    pref = time_signal.get("time_preference", "")
    pref_label = {
        "recent": "（最近的在前）",
        "earliest": "（最早的在前）",
        "chronological": "（按时间线排列）",
    }.get(pref, "")

    lines = [f"[时间感知记忆{pref_label}]"]
    for i, mem in enumerate(memories, 1):
        content = mem.get("content", "")
        date = mem["_parsed_date"].strftime("%Y/%m/%d")
        lines.append(f"{i}. [{date}] {content}")

    return "\n".join(lines)


# ============================================================
# P0-3: 知识更新检测
# ============================================================

# 需要检测知识更新的实体类型
_UPDATABLE_PATTERNS = [
    # 住所
    (r"(?:live|lived|move|moved|relocate|relocated)\s+(?:in|to|at)\s+([\w\s]+)", "location"),
    (r"(?:住|搬到|居住在|搬家到)\s*([\w\u4e00-\u9fff]+)", "location"),
    # 工作
    (r"(?:work|worked|join|joined|got a job)\s+(?:at|in)\s+([\w\s]+)", "organization"),
    (r"(?:在.*工作|加入|入职|就职于)\s*([\w\u4e00-\u9fff]+)", "organization"),
    # 职位
    (r"(?:promote|promoted|become|became)\s+(?:to\s+)?([\w\s]+(?:engineer|manager|director))", "title"),
    (r"(?:升职|晋升|成为)\s*([\w\u4e00-\u9fff]+)", "title"),
    # 状态
    (r"(?:change|changed|switch|switched)\s+(?:to|from)\s+([\w\s]+)", "status"),
    (r"(?:改为|切换到|换成)\s*([\w\u4e00-\u9fff]+)", "status"),
]

# 标记为已过时的字段值
_SUPERSEDED_STATUS = "superseded"


def detect_knowledge_update(
    user_id: int,
    new_content: str,
    workspace_id: Optional[int] = None,
    similarity_threshold: float = 0.6,
) -> Dict[str, Any]:
    """检测新记忆是否与已有记忆构成知识更新。

    当用户说"我搬到旧金山了"而已有记忆"我住纽约"时，
    将旧记忆标记为 "superseded"。

    Args:
        user_id: 用户 ID
        new_content: 新记忆的内容
        workspace_id: workspace ID
        similarity_threshold: 语义相似度阈值

    Returns:
        {
            "success": bool,
            "updated": bool,           # 是否检测到知识更新
            "superseded_ids": List[int], # 被标记为过时的记忆 ID
            "update_type": str,         # location/organization/title/status/none
            "new_value": str,           # 新值
        }
    """
    try:
        # 1. 从新内容中提取可更新实体
        entity = _extract_updatable_entity(new_content)
        if not entity:
            return {
                "success": True,
                "updated": False,
                "superseded_ids": [],
                "update_type": "none",
                "new_value": "",
            }

        update_type, new_value = entity

        # 2. 查找同类型的已有记忆
        db = get_db_client()
        rows = db.execute(
            """SELECT id, content, lifecycle_status FROM memory_fragments
               WHERE user_id = ? AND lifecycle_status = 'active'
               ORDER BY created_at DESC""",
            (user_id,),
        )

        if not rows:
            return {
                "success": True,
                "updated": False,
                "superseded_ids": [],
                "update_type": update_type,
                "new_value": new_value,
            }

        # 3. 在已有记忆中查找同类型但不同值的记忆
        superseded_ids: List[int] = []
        for row in rows:
            old_content = row["content"]
            old_entity = _extract_updatable_entity(old_content)
            if not old_entity:
                continue
            old_type, old_value = old_entity

            # 同类型但不同值 → 知识更新
            if old_type == update_type and old_value.lower() != new_value.lower():
                # 检查语义相似度（简单词重叠）
                similarity = _text_similarity(old_content, new_content)
                if similarity >= similarity_threshold:
                    superseded_ids.append(row["id"])

        # 4. 标记旧记忆为 "superseded"
        if superseded_ids:
            for mid in superseded_ids:
                db.execute(
                    """UPDATE memory_fragments
                       SET lifecycle_status = ?
                       WHERE id = ? AND user_id = ?""",
                    (_SUPERSEDED_STATUS, mid, user_id),
                )
            logger.info(
                f"✓ 知识更新检测: 类型={update_type}, 新值='{new_value}', "
                f"标记 {len(superseded_ids)} 条旧记忆为过时"
            )

        return {
            "success": True,
            "updated": len(superseded_ids) > 0,
            "superseded_ids": superseded_ids,
            "update_type": update_type,
            "new_value": new_value,
        }

    except Exception as e:
        logger.error(f"知识更新检测失败: {e}")
        return {
            "success": False,
            "error": str(e),
            "updated": False,
            "superseded_ids": [],
            "update_type": "none",
            "new_value": "",
        }


def _extract_updatable_entity(content: str) -> Optional[Tuple[str, str]]:
    """从内容中提取可更新的实体（类型, 值）。

    Returns:
        ("location", "San Francisco") 或 None
    """
    content_lower = content.lower()
    for pattern, entity_type in _UPDATABLE_PATTERNS:
        match = re.search(pattern, content, re.IGNORECASE)
        if match:
            value = match.group(1).strip()
            # 清理常见后缀（使用词边界避免误匹配，如 "San" 中的 "an"）
            value = re.sub(r'\s+\b(?:as|in|at|the|a|an)\b\s+.*$', '', value, flags=re.IGNORECASE)
            value = value.rstrip('.,;')
            if len(value) > 1:
                return (entity_type, value)
    return None


def _text_similarity(text1: str, text2: str) -> float:
    """计算两段文本的简单相似度（Jaccard 系数）。"""
    words1 = set(re.findall(r'\w+', text1.lower()))
    words2 = set(re.findall(r'\w+', text2.lower()))
    if not words1 or not words2:
        return 0.0
    intersection = words1 & words2
    union = words1 | words2
    return len(intersection) / len(union) if union else 0.0


def filter_superseded_memories(memories: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """过滤掉已标记为 "superseded" 的记忆。

    在召回结果中排除旧版记忆，只保留最新版本。

    Args:
        memories: 召回的记忆列表

    Returns:
        过滤后的记忆列表
    """
    return [
        m for m in memories
        if m.get("lifecycle_status", "active") != _SUPERSEDED_STATUS
    ]


# ============================================================
# 统一高级召回入口
# ============================================================

def advanced_recall(
    user_id: int,
    question: str,
    top_k: int = 10,
    workspace_id: Optional[int] = None,
) -> Dict[str, Any]:
    """高级召回入口 — 自动选择最优召回策略。

    根据问题类型自动选择：
    - 时间推理问题 → 时间感知召回
    - 多会话推理问题 → 多会话聚合召回
    - 其他问题 → 标准召回（并过滤已过时记忆）

    Args:
        user_id: 用户 ID
        question: 问题
        top_k: 召回条数
        workspace_id: workspace ID

    Returns:
        召回结果（含 memories, context, source 等字段）
    """
    # 优先级：时间感知 > 多会话聚合 > 标准
    if is_temporal_question(question):
        logger.info(f"高级召回: 选择时间感知策略 (问题='{question[:50]}')")
        result = time_aware_recall(user_id, question, top_k, workspace_id)
    elif is_multi_session_question(question):
        logger.info(f"高级召回: 选择多会话聚合策略 (问题='{question[:50]}')")
        result = multi_session_recall(user_id, question, top_k, workspace_id)
    else:
        # 标准召回 + 过滤已过时记忆
        logger.info(f"高级召回: 选择标准策略 (问题='{question[:50]}')")
        from app.services.auto_recall_service import search_relevant_memories
        recall_result = search_relevant_memories(
            user_id=user_id,
            query=question,
            top_k=top_k,
            threshold=0.3,
            workspace_id=workspace_id,
        )
        if recall_result.get("success"):
            memories = filter_superseded_memories(recall_result.get("memories", []))
            context = _format_standard_context(memories)
            result = {
                "success": True,
                "memories": memories,
                "context": context,
                "source": "standard_filtered",
                "memory_count": len(memories),
            }
        else:
            result = {
                "success": False,
                "error": recall_result.get("error", "recall failed"),
                "memories": [],
                "context": "",
                "source": "standard_filtered",
            }

    return result


def _format_standard_context(memories: List[Dict[str, Any]]) -> str:
    """格式化标准召回的上下文。"""
    if not memories:
        return ""
    lines = ["[相关记忆]"]
    for i, mem in enumerate(memories, 1):
        content = mem.get("content", "")
        lines.append(f"{i}. {content}")
    return "\n".join(lines)


# ============================================================
# P1-1: 会话分解索引 (Session Decomposition Indexing)
# ============================================================

# 无意义的短句/问候语过滤词
_NOISE_PATTERNS = [
    r'^(hi|hello|hey|thanks|thank you|ok|okay|sure|yes|no)\b',
    r'^(你好|谢谢|好的|嗯|是的|不是|没关系)\b',
    r'^(that|this|it|there)\s+(is|are|was|were)\s+(great|nice|cool|good|fine)',
    r'^(很高兴|不错|很好|太棒了)',
    r'^\s*$',  # 空白
]

# 事实句子的特征：包含动词且长度适中
_FACT_SENTENCE_MIN_LEN = 10
_FACT_SENTENCE_MAX_LEN = 300


def decompose_session(session: List[Dict[str, Any]]) -> List[str]:
    """将长会话分解为原子记忆单元。

    LongMemEval 论文 CP1 优化：将长会话拆分为细粒度记忆单元，
    每个单元包含一个独立事实，提升特定信息的可检索性。

    策略：
    1. 仅处理用户消息（助手消息不存储为记忆）
    2. 按句子分割（句号/问号/感叹号/换行）
    3. 过滤问候语、短句、无意义句子
    4. 每个剩余句子作为一个原子记忆单元

    Args:
        session: 会话 turn 列表 [{"role": "user", "content": "..."}, ...]

    Returns:
        原子记忆单元列表（每个是一条独立事实）
    """
    atomic_facts: List[str] = []

    for turn in session:
        if turn.get("role") != "user":
            continue
        content = turn.get("content", "").strip()
        if not content:
            continue

        # 按多种标点分割句子
        sentences = re.split(r'[。！？.!?\n]+', content)
        for sent in sentences:
            sent = sent.strip()
            if not sent:
                continue
            # 长度过滤
            if len(sent) < _FACT_SENTENCE_MIN_LEN or len(sent) > _FACT_SENTENCE_MAX_LEN:
                continue
            # 噪声过滤
            if _is_noise_sentence(sent):
                continue
            atomic_facts.append(sent)

    return atomic_facts


def _is_noise_sentence(sentence: str) -> bool:
    """判断是否为无意义的噪声句子。"""
    for pattern in _NOISE_PATTERNS:
        if re.search(pattern, sentence, re.IGNORECASE):
            return True
    return False


def ingest_session_decomposed(
    user_id: int,
    session: List[Dict[str, Any]],
    session_id: str = "",
    session_date: str = "",
    workspace_id: Optional[int] = None,
    importance_score: float = 0.6,
) -> Dict[str, Any]:
    """将会话分解后存入记忆系统（P1-1 优化的摄入入口）。

    采用双存储策略：
    1. 存储原始完整消息（保持语义完整性，确保语义召回能找到）
    2. 存储分解后的原子事实（提升细粒度信息的精确召回）

    Args:
        user_id: 用户 ID
        session: 会话 turn 列表
        session_id: 会话 ID（用于日志）
        session_date: 会话日期（附加到记忆内容）
        workspace_id: workspace ID
        importance_score: 重要性分数

    Returns:
        {"success": bool, "stored": int, "fragments": List[str]}
    """
    from app.services.memory_fragment_service import create_fragment

    atomic_facts = decompose_session(session)

    # 提取原始用户消息
    original_messages: List[str] = []
    for turn in session:
        if turn.get("role") != "user":
            continue
        content = turn.get("content", "").strip()
        if content:
            original_messages.append(content)

    # 如果分解后没有原子事实，使用原始消息
    if not atomic_facts:
        atomic_facts = original_messages[:]

    stored_fragments: List[str] = []

    # 1. 存储原始完整消息（保持语义完整性）
    for msg in original_messages:
        memory_content = f"[{session_date}] {msg}" if session_date else msg
        try:
            result = create_fragment(
                user_id=user_id,
                fragment_type="info",
                content=memory_content,
                importance_score=importance_score,
                workspace_id=workspace_id,
            )
            if result.get("success"):
                stored_fragments.append(memory_content)
                _apply_post_store_optimizations(
                    user_id, result.get("fragment_id"),
                    memory_content, session_date, workspace_id,
                )
        except Exception as e:
            logger.debug(f"存储原始消息失败 (session={session_id}): {e}")

    # 2. 存储分解后的原子事实（仅当分解出多条时才需要，单条时与原始消息重复）
    if len(atomic_facts) > 1:
        for fact in atomic_facts:
            memory_content = f"[{session_date}] {fact}" if session_date else fact
            try:
                result = create_fragment(
                    user_id=user_id,
                    fragment_type="info",
                    content=memory_content,
                    importance_score=max(0.1, importance_score - 0.2),  # 原子事实重要性略低
                    workspace_id=workspace_id,
                )
                if result.get("success"):
                    stored_fragments.append(memory_content)
                    _apply_post_store_optimizations(
                        user_id, result.get("fragment_id"),
                        memory_content, session_date, workspace_id,
                    )
            except Exception as e:
                logger.debug(f"存储原子事实失败 (session={session_id}): {e}")

    logger.info(
        f"✓ 会话分解索引: session={session_id}, "
        f"原始消息 {len(original_messages)} 条, 原子事实 {len(atomic_facts)} 条, "
        f"共存储 {len(stored_fragments)} 条"
    )

    return {
        "success": True,
        "stored": len(stored_fragments),
        "fragments": stored_fragments,
    }


def _apply_post_store_optimizations(
    user_id: int,
    fragment_id: Optional[int],
    memory_content: str,
    session_date: str,
    workspace_id: Optional[int],
) -> None:
    """应用存储后优化：知识更新检测 + 多键索引生成。"""
    # P0-3: 知识更新检测
    try:
        detect_knowledge_update(
            user_id=user_id,
            new_content=memory_content,
            workspace_id=workspace_id,
        )
    except Exception as e:
        logger.debug(f"知识更新检测失败: {e}")

    # P1-2: 生成多键索引
    if fragment_id is not None:
        try:
            generate_search_keys(
                fragment_id=fragment_id,
                user_id=user_id,
                content=memory_content,
                session_date=session_date,
                workspace_id=workspace_id,
            )
        except Exception as e:
            logger.debug(f"多键索引生成失败: {e}")


# ============================================================
# P1-2: 多键索引 (Multi-key Indexing)
# ============================================================

# 检索键类型
_KEY_TYPE_FACT = "fact"
_KEY_TYPE_SEMANTIC = "semantic"
_KEY_TYPE_TIME = "time"

# 多键索引表名
_SEARCH_KEYS_TABLE = "memory_search_keys"


def _ensure_search_keys_table() -> None:
    """确保多键索引表存在。"""
    db = get_db_client()
    db.execute(f'''
        CREATE TABLE IF NOT EXISTS {_SEARCH_KEYS_TABLE} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fragment_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            workspace_id TEXT,
            key_type TEXT NOT NULL,
            key_content TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    try:
        db.execute(f'CREATE INDEX IF NOT EXISTS idx_search_keys_user ON {_SEARCH_KEYS_TABLE}(user_id)')
        db.execute(f'CREATE INDEX IF NOT EXISTS idx_search_keys_type ON {_SEARCH_KEYS_TABLE}(user_id, key_type)')
        db.execute(f'CREATE INDEX IF NOT EXISTS idx_search_keys_fragment ON {_SEARCH_KEYS_TABLE}(fragment_id)')
    except Exception:
        pass


def generate_search_keys(
    fragment_id: int,
    user_id: int,
    content: str,
    session_date: str = "",
    workspace_id: Optional[int] = None,
) -> Dict[str, Any]:
    """为记忆片段生成多个检索键（P1-2 多键索引）。

    每条记忆生成三类检索键：
    - fact_key: 提取的事实关键词（名词/数字/专有名词），用于精确匹配
    - semantic_key: 语义摘要/核心概念，用于语义搜索扩展
    - time_key: 时间相关线索（年份/日期/相对时间），用于时间感知检索

    Args:
        fragment_id: 记忆片段 ID
        user_id: 用户 ID
        content: 记忆内容
        session_date: 会话日期
        workspace_id: workspace ID

    Returns:
        {"success": bool, "keys": {"fact": [...], "semantic": [...], "time": [...]}}
    """
    _ensure_search_keys_table()
    db = get_db_client()

    ws_str = str(workspace_id) if workspace_id is not None else ""

    # 1. 事实键：提取关键词（名词、数字、专有名词）
    fact_keys = _extract_fact_keywords(content)
    # 2. 语义键：生成语义摘要（取核心句）
    semantic_keys = _extract_semantic_concepts(content)
    # 3. 时间键：提取时间线索
    time_keys = _extract_time_keywords(content, session_date)

    all_keys: List[Tuple[str, str]] = []
    for kw in fact_keys:
        all_keys.append((_KEY_TYPE_FACT, kw))
    for kw in semantic_keys:
        all_keys.append((_KEY_TYPE_SEMANTIC, kw))
    for kw in time_keys:
        all_keys.append((_KEY_TYPE_TIME, kw))

    # 批量写入
    for key_type, key_content in all_keys:
        db.execute(
            f'''INSERT INTO {_SEARCH_KEYS_TABLE}
                (fragment_id, user_id, workspace_id, key_type, key_content)
                VALUES (?, ?, ?, ?, ?)''',
            (fragment_id, user_id, ws_str, key_type, key_content),
        )

    return {
        "success": True,
        "keys": {
            "fact": fact_keys,
            "semantic": semantic_keys,
            "time": time_keys,
        },
        "total": len(all_keys),
    }


def _extract_fact_keywords(content: str) -> List[str]:
    """从内容中提取事实关键词（名词、数字、专有名词）。

    用于精确匹配检索。
    """
    keys: List[str] = []

    # 提取数字+单位（如 "5 years", "3 years ago"）
    number_patterns = re.findall(r'\b\d+\s*(?:years?|months?|weeks?|days?|hours?|times?)\b', content, re.IGNORECASE)
    keys.extend(number_patterns[:5])

    # 提取大写首字母词（专有名词，如 Python, Google, Microsoft）
    proper_nouns = re.findall(r'\b[A-Z][a-z]+(?:[A-Z][a-z]+)?\b', content)
    keys.extend(proper_nouns[:5])

    # 提取中文专有名词（2-4字连续汉字，排除常见动词）
    chinese_nouns = re.findall(r'[\u4e00-\u9fff]{2,4}', content)
    # 排除常见动词/形容词
    chinese_stop = {"我是", "我叫", "我有", "我在", "我的", "我们", "这个", "那个", "什么", "怎么", "可以", "应该", "觉得", "认为"}
    chinese_nouns = [n for n in chinese_nouns if n not in chinese_stop]
    keys.extend(chinese_nouns[:5])

    # 去重
    seen = set()
    unique = []
    for k in keys:
        if k.lower() not in seen and len(k) > 1:
            seen.add(k.lower())
            unique.append(k)

    return unique[:10]  # 最多 10 个事实键


def _extract_semantic_concepts(content: str) -> List[str]:
    """从内容中提取语义概念（核心短语）。

    用于语义搜索扩展。
    """
    keys: List[str] = []

    # 提取 "动词 + 名词" 短语（如 "coding in Python", "work at Google"）
    verb_noun = re.findall(
        r'\b(?:live|work|study|learn|code|program|speak|move|join|like|love|prefer)\s+(?:in|at|on|to)?\s+([A-Z][a-z]+|\w+)',
        content, re.IGNORECASE
    )
    keys.extend(verb_noun[:3])

    # 提取 "have been + 动名词" 短语
    have_been = re.findall(r'\bhave been\s+(\w+ing\s+\w+)', content, re.IGNORECASE)
    keys.extend(have_been[:2])

    # 提取核心名词短语（连续 2+ 个英文单词）
    noun_phrases = re.findall(r'\b(?:[a-z]{3,}\s+){1,3}[a-z]{3,}\b', content.lower())
    # 过滤停用词开头的短语
    stop_start = {"the", "and", "for", "with", "that", "this", "have", "been", "from"}
    noun_phrases = [p for p in noun_phrases if p.split()[0] not in stop_start]
    keys.extend(noun_phrases[:3])

    # 去重
    seen = set()
    unique = []
    for k in keys:
        if k.lower() not in seen and len(k) > 2:
            seen.add(k.lower())
            unique.append(k)

    return unique[:5]  # 最多 5 个语义键


def _extract_time_keywords(content: str, session_date: str = "") -> List[str]:
    """从内容中提取时间关键词。

    用于时间感知检索。
    """
    keys: List[str] = []

    # 从内容中提取年份
    years = re.findall(r'\b(20\d{2})\b', content)
    keys.extend(years[:3])

    # 从内容中提取相对时间
    rel_time = re.findall(r'\b(?:last|next|this|previous)\s+(?:year|month|week|day)\b', content, re.IGNORECASE)
    keys.extend(rel_time[:2])

    # 从内容中提取 "X years ago"
    ago = re.findall(r'\b\d+\s+years?\s+ago\b', content, re.IGNORECASE)
    keys.extend(ago[:2])

    # 从会话日期中提取
    if session_date:
        # 提取日期中的年份
        year_in_date = re.findall(r'(20\d{2})', session_date)
        keys.extend(year_in_date[:1])

    # 去重
    seen = set()
    unique = []
    for k in keys:
        if k.lower() not in seen:
            seen.add(k.lower())
            unique.append(k)

    return unique[:5]  # 最多 5 个时间键


def search_by_multi_keys(
    user_id: int,
    query: str,
    top_k: int = 10,
    workspace_id: Optional[int] = None,
) -> Dict[str, Any]:
    """通过多键索引检索记忆（P1-2 多键索引检索）。

    根据查询内容匹配多键索引，召回相关记忆片段 ID，
    然后从 memory_fragments 表获取完整记忆。

    Args:
        user_id: 用户 ID
        query: 查询文本
        top_k: 返回条数
        workspace_id: workspace ID

    Returns:
        {"success": bool, "fragment_ids": List[int], "key_matches": Dict}
    """
    try:
        _ensure_search_keys_table()
        db = get_db_client()

        # 从查询中提取搜索词
        query_fact_keys = _extract_fact_keywords(query)
        query_time_keys = _extract_time_keywords(query)

        all_query_keys = query_fact_keys + query_time_keys

        if not all_query_keys:
            return {"success": True, "fragment_ids": [], "key_matches": {}}

        # 在多键索引表中搜索匹配的 fragment_id
        ws_clause = "AND workspace_id = ?" if workspace_id is not None else "AND (workspace_id = ? OR workspace_id = '')"
        ws_param = str(workspace_id) if workspace_id is not None else ""

        fragment_scores: Dict[int, Dict[str, int]] = {}

        for key in all_query_keys:
            try:
                rows = db.execute(
                    f'''SELECT fragment_id, key_type FROM {_SEARCH_KEYS_TABLE}
                        WHERE user_id = ? {ws_clause}
                        AND key_content LIKE ?''',
                    (user_id, ws_param, f"%{key}%"),
                )
                if rows:
                    for row in rows:
                        fid = row["fragment_id"]
                        if fid not in fragment_scores:
                            fragment_scores[fid] = {"fact": 0, "semantic": 0, "time": 0, "total": 0}
                        ktype = row["key_type"]
                        if ktype in fragment_scores[fid]:
                            fragment_scores[fid][ktype] += 1
                        fragment_scores[fid]["total"] += 1
            except Exception:
                continue

        if not fragment_scores:
            return {"success": True, "fragment_ids": [], "key_matches": {}}

        # 按匹配分数排序
        sorted_fids = sorted(
            fragment_scores.items(),
            key=lambda x: (x[1]["total"], x[1]["fact"], x[1]["time"]),
            reverse=True,
        )

        top_fids = [fid for fid, _ in sorted_fids[:top_k]]

        return {
            "success": True,
            "fragment_ids": top_fids,
            "key_matches": {fid: scores for fid, scores in sorted_fids[:top_k]},
            "total_matched": len(fragment_scores),
        }

    except Exception as e:
        logger.error(f"多键索引检索失败: {e}")
        return {"success": False, "error": str(e), "fragment_ids": [], "key_matches": {}}


# ============================================================
# P1-3: 时间感知查询扩展 (Time-aware Query Expansion)
# ============================================================

# 时间扩展词映射
_TIME_EXPANSION_TERMS = {
    "recent": ["recently", "latest", "last", "current", "now"],
    "earliest": ["first", "initial", "original", "start", "begin", "earliest"],
    "chronological": ["timeline", "sequence", "order", "chronological", "history"],
    "before": ["before", "prior", "earlier", "preceding"],
    "after": ["after", "since", "following", "subsequent"],
}


def expand_query_with_time(question: str) -> List[str]:
    """从问题中提取时间线索，生成时间扩展查询（P1-3）。

    在 P0-2 时间感知召回的基础上，P1-3 在查询阶段就扩展查询词，
    使语义搜索能召回更多时间相关的记忆。

    策略：
    1. 提取时间偏好（复用 P0-2 的 extract_time_signals）
    2. 根据时间偏好生成扩展查询
    3. 返回 [原始查询] + [扩展查询] 列表

    Args:
        question: 原始问题

    Returns:
        扩展查询列表（至少包含原始查询）
    """
    time_signal = extract_time_signals(question)
    preference = time_signal.get("time_preference")

    if not preference:
        return [question]

    expanded_queries: List[str] = [question]

    # 根据时间偏好生成扩展查询
    if preference == "recent":
        # 最近的：添加 "latest/current/last" 扩展词
        for term in _TIME_EXPANSION_TERMS["recent"]:
            expanded = f"{term} {question}"
            if expanded not in expanded_queries:
                expanded_queries.append(expanded)

    elif preference == "earliest":
        # 最早的：添加 "first/initial/start" 扩展词
        for term in _TIME_EXPANSION_TERMS["earliest"]:
            expanded = f"{term} {question}"
            if expanded not in expanded_queries:
                expanded_queries.append(expanded)

    elif preference == "chronological":
        # 时间线：添加排序相关词
        for term in _TIME_EXPANSION_TERMS["chronological"]:
            expanded = f"{term} {question}"
            if expanded not in expanded_queries:
                expanded_queries.append(expanded)

    elif preference.startswith("before:"):
        # 某年之前：添加年份和 "before/prior" 扩展
        year = preference.split(":")[1]
        expanded_queries.append(f"before {year}")
        expanded_queries.append(f"prior to {year}")
        expanded_queries.append(f"{question} {year}")

    elif preference.startswith("after:"):
        # 某年之后：添加年份和 "after/since" 扩展
        year = preference.split(":")[1]
        expanded_queries.append(f"after {year}")
        expanded_queries.append(f"since {year}")
        expanded_queries.append(f"{question} {year}")

    elif preference.startswith("year:"):
        # 特定年份：添加年份扩展
        year = preference.split(":")[1]
        expanded_queries.append(f"{question} {year}")
        expanded_queries.append(f"in {year}")

    # 限制扩展查询数量
    return expanded_queries[:6]


def time_expanded_recall(
    user_id: int,
    question: str,
    top_k: int = 10,
    workspace_id: Optional[int] = None,
) -> Dict[str, Any]:
    """时间感知查询扩展召回（P1-3 完整实现）。

    在 P0-2 时间感知召回的基础上，使用查询扩展提升召回率：
    1. 对原始问题生成时间扩展查询
    2. 对每个扩展查询执行语义召回
    3. 合并去重后按时间偏好排序

    Args:
        user_id: 用户 ID
        question: 问题
        top_k: 召回条数
        workspace_id: workspace ID

    Returns:
        {
            "success": bool,
            "memories": List[Dict],
            "context": str,
            "expanded_queries": List[str],
            "source": "time_expanded",
        }
    """
    try:
        from app.services.auto_recall_service import search_relevant_memories

        expanded_queries = expand_query_with_time(question)
        time_signal = extract_time_signals(question)
        preference = time_signal.get("time_preference")

        all_memories: List[Dict[str, Any]] = []
        seen_ids = set()

        for eq in expanded_queries:
            result = search_relevant_memories(
                user_id=user_id,
                query=eq,
                top_k=top_k,
                threshold=0.2,  # 扩展查询使用较低阈值
                workspace_id=workspace_id,
            )
            if not result.get("success"):
                continue
            for mem in result.get("memories", []):
                mem_id = mem.get("id") or mem.get("content", "")[:50]
                if mem_id not in seen_ids:
                    seen_ids.add(mem_id)
                    mem["_expanded_query"] = eq
                    all_memories.append(mem)

        # 解析时间戳并按偏好排序
        for mem in all_memories:
            mem["_parsed_date"] = _parse_memory_date(mem)

        if preference == "recent":
            all_memories.sort(key=lambda m: m["_parsed_date"], reverse=True)
        elif preference in ("earliest", "chronological"):
            all_memories.sort(key=lambda m: m["_parsed_date"])

        # 截断
        all_memories = all_memories[:top_k]

        # 格式化上下文
        context = _format_temporal_context(all_memories, time_signal)

        logger.info(
            f"✓ 时间扩展召回: {len(expanded_queries)} 扩展查询, "
            f"召回 {len(all_memories)} 条记忆"
        )

        return {
            "success": True,
            "memories": all_memories,
            "context": context,
            "expanded_queries": expanded_queries,
            "time_signal": time_signal,
            "source": "time_expanded",
            "memory_count": len(all_memories),
        }

    except Exception as e:
        logger.error(f"时间扩展召回失败: {e}")
        return {
            "success": False,
            "error": str(e),
            "memories": [],
            "context": "",
            "expanded_queries": [],
            "source": "time_expanded",
        }


# ============================================================
# P1 集成：增强版高级召回入口
# ============================================================

def advanced_recall_v2(
    user_id: int,
    question: str,
    top_k: int = 10,
    workspace_id: Optional[int] = None,
) -> Dict[str, Any]:
    """增强版高级召回入口 — 集成 P0 + P1 优化。

    在 P0 advanced_recall 基础上叠加 P1 优化：
    - P1-2: 多键索引检索（作为补充信号，不破坏语义排序）
    - P1-3: 时间感知查询扩展（替代 P0-2 的简单时间排序）

    设计原则（非破坏性增强）：
    - 标准问题：直接委托 P0 advanced_recall，保持 100% 准确率
    - 时间问题：使用 P1-3 查询扩展提升召回率
    - 多会话问题：使用 P0-1 聚合 + P1-2 多键补充
    - 多键匹配仅作为标注信号，不重排语义搜索结果

    Args:
        user_id: 用户 ID
        question: 问题
        top_k: 召回条数
        workspace_id: workspace ID

    Returns:
        召回结果（含 memories, context, source 等字段）
    """
    # 根据问题类型选择主策略
    if is_temporal_question(question):
        # P1-3: 时间感知查询扩展召回（替代 P0-2）
        logger.info(f"高级召回v2: 选择时间扩展策略 (问题='{question[:50]}')")
        result = time_expanded_recall(user_id, question, top_k, workspace_id)

        # P1-2: 标注多键匹配（不重排，仅标注）
        _annotate_key_matches(result, user_id, question, top_k, workspace_id)
        result["p1_optimized"] = True
        return result

    elif is_multi_session_question(question):
        # P0-1: 多会话聚合
        logger.info(f"高级召回v2: 选择多会话聚合策略 (问题='{question[:50]}')")
        result = multi_session_recall(user_id, question, top_k, workspace_id)

        # P1-2: 标注多键匹配（不重排，仅标注）
        _annotate_key_matches(result, user_id, question, top_k, workspace_id)
        result["p1_optimized"] = True
        return result

    else:
        # 标准问题：直接委托 P0 advanced_recall，保持最佳准确率
        logger.info(f"高级召回v2: 委托 P0 标准策略 (问题='{question[:50]}')")
        result = advanced_recall(
            user_id=user_id,
            question=question,
            top_k=top_k,
            workspace_id=workspace_id,
        )

        # P1-2: 标注多键匹配（不重排，仅标注）
        _annotate_key_matches(result, user_id, question, top_k, workspace_id)
        result["p1_optimized"] = True
        return result


def _annotate_key_matches(
    result: Dict[str, Any],
    user_id: int,
    question: str,
    top_k: int,
    workspace_id: Optional[int],
) -> None:
    """标注多键匹配的记忆（不重排，保持语义搜索排序）。

    P1-2 多键索引仅作为标注信号，不改变语义搜索的排序结果。
    这样既保留了多键索引的检索能力，又不破坏 P0 的准确率。
    """
    if not result.get("success") or not result.get("memories"):
        return

    try:
        multi_key_result = search_by_multi_keys(
            user_id=user_id,
            query=question,
            top_k=top_k,
            workspace_id=workspace_id,
        )
        key_matched_ids = set(multi_key_result.get("fragment_ids", []))
        if key_matched_ids:
            count = 0
            for mem in result["memories"]:
                mem_id = mem.get("id")
                if mem_id and mem_id in key_matched_ids:
                    mem["_key_matched"] = True
                    count += 1
            result["key_matched_count"] = count
    except Exception as e:
        logger.debug(f"多键标注失败: {e}")

