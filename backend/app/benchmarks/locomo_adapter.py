"""
LoCoMo 数据集适配器

LoCoMo（Long Conversational Memory）是 Snap Research 发布的长期记忆基准，
包含 1540 道问题，覆盖多轮对话长期记忆。

评测维度：
    - 记忆提取（extraction）
    - 时间推理（temporal_reasoning）
    - 弃权（abstention）
    - 多跳推理（multi_hop）

与 LongMemEval 互补：LongMemEval 侧重会话级，LoCoMo 侧重多跳推理。

数据集获取：
    git clone https://github.com/snap-research/locomo.git
    数据文件放入 backend/app/benchmarks/data/locomo/

数据集格式（标准化后）:
    {
        "question_id": "locomo_001",
        "question_type": "extraction",
        "question": "What did the user say about...",
        "answer": "...",
        "haystack_sessions": [[{"role": "user", "content": "..."}, ...], ...],
        "haystack_dates": ["2024-01-01", ...],
        "haystack_session_ids": ["s1", ...]
    }

注：LoCoMo 原始数据格式可能不同，load_locomo_dataset() 负责标准化。
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# 默认数据目录
_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "locomo")


# ============================================================
# 能力类别映射
# ============================================================

# LoCoMo 的 4 个维度 → 标准能力字段
LOCOMO_CATEGORY_TO_ABILITY = {
    "extraction": "information_extraction",
    "temporal": "temporal_reasoning",
    "abstention": "abstention",
    "multi_hop": "multi_hop_reasoning",
    # 兼容其他可能的命名
    "extract": "information_extraction",
    "temporal_reasoning": "temporal_reasoning",
    "multi-hop": "multi_hop_reasoning",
    "multihop": "multi_hop_reasoning",
}

ABILITY_LABELS = {
    "information_extraction": "记忆提取",
    "temporal_reasoning": "时间推理",
    "abstention": "弃权",
    "multi_hop_reasoning": "多跳推理",
}


def get_ability(instance: Dict[str, Any]) -> str:
    """从 LoCoMo 实例中提取记忆能力类别。"""
    qid = instance.get("question_id", "")
    if qid.endswith("_abs") or instance.get("category", "").lower() == "abstention":
        return "abstention"
    category = instance.get("category", "") or instance.get("question_type", "")
    return LOCOMO_CATEGORY_TO_ABILITY.get(category.lower(), "unknown")


# ============================================================
# 数据集加载与标准化
# ============================================================

def load_locomo_dataset(data_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """加载 LoCoMo 数据集并标准化为统一格式。

    LoCoMo 原始数据可能是 JSON 或 JSONL，每条包含 conversation + question。
    本函数将其标准化为与 LongMemEval 相同的结构。

    Args:
        data_path: 数据文件路径或目录。
                   若为目录，自动查找 locomo_data.json 或 *.json。

    Returns:
        标准化实例列表
    """
    path = data_path or _DATA_DIR
    if os.path.isdir(path):
        # 在目录中查找数据文件
        candidates = ["locomo_data.json", "locomo.json", "data.json", "questions.json"]
        found = None
        for candidate in candidates:
            full_path = os.path.join(path, candidate)
            if os.path.exists(full_path):
                found = full_path
                break
        if not found:
            # 查找任意 JSON 文件
            for f in os.listdir(path):
                if f.endswith(".json"):
                    found = os.path.join(path, f)
                    break
        if not found:
            raise FileNotFoundError(
                f"LoCoMo 数据集未找到: {path}\n"
                f"下载: git clone https://github.com/snap-research/locomo.git "
                f"并将数据文件放入 {_DATA_DIR}"
            )
        path = found

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # 标准化
    if isinstance(data, list):
        instances = [_normalize_instance(item, i) for i, item in enumerate(data)]
    elif isinstance(data, dict):
        # 可能是 {"questions": [...]} 或 {"conversations": [...], "questions": [...]}
        if "questions" in data:
            instances = [_normalize_instance(item, i) for i, item in enumerate(data["questions"])]
        elif "data" in data:
            instances = [_normalize_instance(item, i) for i, item in enumerate(data["data"])]
        else:
            # 单条实例
            instances = [_normalize_instance(data, 0)]
    else:
        raise ValueError(f"LoCoMo 数据集格式错误：期望 list 或 dict，得到 {type(data).__name__}")

    logger.info(f"加载 LoCoMo 数据集: {path}，共 {len(instances)} 条实例")
    return instances


def _normalize_instance(raw: Dict[str, Any], idx: int) -> Dict[str, Any]:
    """将 LoCoMo 原始实例标准化为统一格式。

    兼容多种 LoCoMo 数据格式变体：
    - conversation + question 字段
    - haystack_sessions 已标准化格式
    """
    qid = raw.get("question_id") or raw.get("id") or f"locomo_{idx:04d}"

    # 会话历史：LoCoMo 可能用 conversation/sessions/haystack_sessions
    sessions = (
        raw.get("haystack_sessions")
        or raw.get("sessions")
        or _extract_sessions_from_conversation(raw.get("conversation", []))
    )

    # 日期
    dates = raw.get("haystack_dates") or raw.get("dates") or []
    session_ids = raw.get("haystack_session_ids") or raw.get("session_ids") or [
        f"locomo_s{i}" for i in range(len(sessions))
    ]

    # 问题类型
    category = (
        raw.get("question_type")
        or raw.get("category")
        or raw.get("type")
        or "extraction"
    )

    return {
        "question_id": qid,
        "question_type": category,
        "question": raw.get("question", ""),
        "answer": raw.get("answer", ""),
        "question_date": raw.get("question_date", ""),
        "haystack_sessions": sessions,
        "haystack_dates": dates,
        "haystack_session_ids": session_ids,
        "category": category,
    }


def _extract_sessions_from_conversation(conversation: Any) -> List[List[Dict[str, Any]]]:
    """从 LoCoMo 的 conversation 字段提取会话列表。

    LoCoMo 的 conversation 可能是：
    - 单个会话（turn 列表）
    - 多个会话的列表
    """
    if not conversation:
        return []

    # 单个会话（turn 列表）
    if isinstance(conversation, list) and conversation and isinstance(conversation[0], dict):
        if "role" in conversation[0]:
            return [conversation]

    # 多个会话
    if isinstance(conversation, list) and conversation and isinstance(conversation[0], list):
        return conversation

    return []


# ============================================================
# 会话文本化（复用 LongMemEval 的逻辑）
# ============================================================

def session_to_text(session: List[Dict[str, Any]], include_assistant: bool = True) -> str:
    """将会话（turn 列表）转为纯文本。"""
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
    """从会话中提取用户陈述的事实。"""
    user_messages = []
    for turn in session:
        if turn.get("role") == "user":
            user_messages.append(turn.get("content", ""))
    return "\n".join(user_messages)


# ============================================================
# 数据集统计
# ============================================================

def dataset_stats(instances: List[Dict[str, Any]]) -> Dict[str, Any]:
    """计算数据集统计信息。"""
    stats: Dict[str, Any] = {
        "total_instances": len(instances),
        "by_category": {},
        "total_sessions": 0,
        "total_turns": 0,
        "avg_sessions_per_instance": 0.0,
    }

    for inst in instances:
        ability = get_ability(inst)
        if ability not in stats["by_category"]:
            stats["by_category"][ability] = {"count": 0, "label": ABILITY_LABELS.get(ability, ability)}
        stats["by_category"][ability]["count"] += 1

        sessions = inst.get("haystack_sessions", [])
        stats["total_sessions"] += len(sessions)
        for session in sessions:
            stats["total_turns"] += len(session)

    stats["avg_sessions_per_instance"] = (
        stats["total_sessions"] / len(instances) if instances else 0.0
    )
    return stats
