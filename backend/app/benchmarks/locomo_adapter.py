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

# 默认数据目录（app/benchmarks/data/locomo）
_DATA_DIR = os.path.join(os.path.dirname(__file__), "data", "locomo")


# ============================================================
# 能力类别映射
# ============================================================

# LoCoMo 的维度 → 标准能力字段
# 原始数据（locomo10.json）用数字 category：
#   1=多跳推理 2=时间推理 3=开放域知识 4=单跳提取 5=对抗/弃权
# （与 Mem0/Zep 评测口径一致：1540 题 = 全部 1986 题剔除 category 5）
LOCOMO_CATEGORY_TO_ABILITY = {
    "1": "multi_hop_reasoning",
    "2": "temporal_reasoning",
    "3": "open_domain_knowledge",
    "4": "information_extraction",
    "5": "abstention",
    # 兼容文本命名
    "extraction": "information_extraction",
    "extract": "information_extraction",
    "single_hop": "information_extraction",
    "single-hop": "information_extraction",
    "temporal": "temporal_reasoning",
    "temporal_reasoning": "temporal_reasoning",
    "abstention": "abstention",
    "adversarial": "abstention",
    "open_domain": "open_domain_knowledge",
    "open-domain": "open_domain_knowledge",
    "multi_hop": "multi_hop_reasoning",
    "multi-hop": "multi_hop_reasoning",
    "multihop": "multi_hop_reasoning",
}

ABILITY_LABELS = {
    "information_extraction": "单跳提取",
    "temporal_reasoning": "时间推理",
    "open_domain_knowledge": "开放域知识",
    "multi_hop_reasoning": "多跳推理",
    "abstention": "弃权",
}


def get_ability(instance: Dict[str, Any]) -> str:
    """从 LoCoMo 实例中提取记忆能力类别。"""
    qid = instance.get("question_id", "")
    if qid.endswith("_abs") or str(instance.get("category", "")).lower() == "abstention":
        return "abstention"
    category = str(instance.get("category", "") or instance.get("question_type", ""))
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
        if data and isinstance(data[0], dict) and "qa" in data[0] and "conversation" in data[0]:
            # LoCoMo 官方原始格式（locomo10.json）：每个样本 = 一段长对话 + 多道 QA
            instances = []
            for sample in data:
                instances.extend(_expand_locomo_sample(sample))
        else:
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


def _expand_locomo_sample(sample: Dict[str, Any]) -> List[Dict[str, Any]]:
    """将 LoCoMo 原始样本（一段对话 + 多道 QA）展开为标准实例列表。

    原始格式：
        {
            "sample_id": "conv-26",
            "conversation": {
                "speaker_a": "Caroline", "speaker_b": "Melanie",
                "session_1_date_time": "1:56 pm on 8 May, 2023",
                "session_1": [{"speaker": "Caroline", "dia_id": "D1:1", "text": "..."}, ...],
                ...
            },
            "qa": [{"question": "...", "answer": "...", "category": 2, ...}, ...]
        }

    同一样本的所有 QA 共享同一段对话，因此每条实例携带相同的
    haystack_group（=sample_id），runner 可据此复用已摄入的记忆。
    双人对话的两位 speaker 均映射为 role=user（内容前缀 speaker 名），
    避免记忆摄入时被当作 assistant 回复而丢弃。
    """
    sample_id = sample.get("sample_id", "locomo")
    conv = sample.get("conversation", {})

    # 按 session 序号排序（session_1, session_2, ...）
    session_nums = sorted(
        int(k.split("_")[1]) for k in conv
        if k.startswith("session_") and not k.endswith("_date_time") and isinstance(conv.get(k), list)
    )

    sessions: List[List[Dict[str, Any]]] = []
    dates: List[str] = []
    session_ids: List[str] = []
    for n in session_nums:
        turns = []
        for turn in conv.get(f"session_{n}", []):
            speaker = turn.get("speaker", "")
            text = (turn.get("text") or "").strip()
            caption = (turn.get("blip_caption") or "").strip()
            if caption:
                text = f"{text} [分享图片: {caption}]" if text else f"[分享图片: {caption}]"
            if not text:
                continue
            turns.append({"role": "user", "content": f"{speaker}: {text}"})
        sessions.append(turns)
        dates.append(conv.get(f"session_{n}_date_time", ""))
        session_ids.append(f"{sample_id}_s{n}")

    instances: List[Dict[str, Any]] = []
    for i, qa in enumerate(sample.get("qa", [])):
        category = str(qa.get("category", ""))
        is_adversarial = category == "5" or "adversarial_answer" in qa
        qid = f"{sample_id}_q{i:03d}" + ("_abs" if is_adversarial else "")
        answer = qa.get("answer")
        if answer is None:
            answer = qa.get("adversarial_answer", "")
        instances.append({
            "question_id": qid,
            "question_type": category,
            "question": qa.get("question", ""),
            "answer": str(answer),
            "question_date": "",
            "haystack_sessions": sessions,
            "haystack_dates": dates,
            "haystack_session_ids": session_ids,
            "category": category,
            "haystack_group": sample_id,
            "evidence": qa.get("evidence", ""),
        })
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
