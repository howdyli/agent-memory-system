"""
LongMemEval 评估器

使用 LLM Judge 评估生成答案的正确性，并计算分类别准确率。

评估方式（遵循 LongMemEval 官方协议）:
    1. QA 正确性：LLM Judge 判断生成答案是否与参考答案语义一致
    2. 弃权问题：正确答案是 "信息不足"，系统应识别无法回答的情况
    3. 分类别统计：按 5 种记忆能力分别计算准确率

LLM Judge Prompt 遵循 LongMemEval 官方实现：
    https://github.com/xiaowu0162/LongMemEval/blob/main/src/evaluation/evaluate_qa.py
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ============================================================
# LLM Judge Prompt（遵循 LongMemEval 官方协议）
# ============================================================

JUDGE_PROMPT_TEMPLATE = """You are an expert judge evaluating the correctness of answers to questions about a user's chat history.

Question: {question}

Reference Answer: {reference_answer}

Model Answer: {model_answer}

Evaluate whether the model answer is correct compared to the reference answer.
- For factual questions: the model answer must contain the key information from the reference answer.
- For abstention questions: the model answer should indicate that the information is not available.
- Minor wording differences are acceptable as long as the core meaning is preserved.

Respond with ONLY a JSON object:
{{"correct": true/false, "reason": "brief explanation"}}"""


# ============================================================
# LLM Judge 调用
# ============================================================

def _call_judge_llm(prompt: str, user_id: int) -> Optional[Dict[str, Any]]:
    """调用 LLM 进行答案评判。

    Returns:
        {"correct": bool, "reason": str} 或 None（LLM 不可用时）
    """
    try:
        from app.services.llm_backend_service import llm_chat

        result = llm_chat(
            user_id=user_id,
            messages=[
                {"role": "system", "content": "You are an expert judge. Respond with ONLY a JSON object."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.0,
            enqueue_on_failure=False,
        )

        if not result.get("success") or not result.get("content"):
            return None

        text = result["content"].strip()

        # 提取 JSON
        code_match = re.search(r'```(?:json)?\s*\n?(\{.*?\})\n?```', text, re.DOTALL)
        if code_match:
            text = code_match.group(1)
        brace_match = re.search(r'\{[^{}]*"correct"[^{}]*\}', text)
        if brace_match:
            text = brace_match.group(0)

        data = json.loads(text)
        return {
            "correct": bool(data.get("correct", False)),
            "reason": data.get("reason", ""),
        }
    except Exception as e:
        logger.debug(f"LLM Judge 调用失败: {e}")
        return None


# ============================================================
# 启发式评判（LLM 不可用时的降级策略）
# ============================================================

def _heuristic_judge(
    question: str,
    reference_answer: str,
    model_answer: str,
    is_abstention: bool = False,
) -> Tuple[bool, str]:
    """启发式答案评判。

    策略：
    1. 弃权问题：检查模型答案是否表示"不知道/信息不足"
    2. 事实问题：检查参考答案的核心词是否出现在模型答案中
    """
    model_lower = model_answer.lower().strip()
    ref_lower = reference_answer.lower().strip()

    # 弃权问题：模型应回答"不知道"
    if is_abstention:
        abstention_indicators = [
            "don't have", "dont have", "not enough", "cannot", "can't",
            "no information", "unable to", "i don't know", "unknown",
            "信息不足", "不知道", "无法回答", "没有相关",
        ]
        is_abstain = any(ind in model_lower for ind in abstention_indicators)
        return (is_abstain, "heuristic: abstention detection")

    # 提取参考答案的关键词
    # 移除常见停用词
    stop_words = {
        "the", "a", "an", "is", "are", "was", "were", "be", "been",
        "to", "of", "in", "on", "at", "for", "with", "and", "or",
        "i", "you", "he", "she", "it", "we", "they",
    }
    ref_words = set(ref_lower.split()) - stop_words
    ref_words = {w for w in ref_words if len(w) > 1}

    if not ref_words:
        # 参考答案为空或无关键词，检查是否部分匹配
        return (ref_lower in model_lower, "heuristic: substring match")

    # 计算关键词命中率
    matched = sum(1 for w in ref_words if w in model_lower)
    hit_rate = matched / len(ref_words) if ref_words else 0

    # 命中率 >= 50% 判为正确
    is_correct = hit_rate >= 0.5
    reason = f"heuristic: {matched}/{len(ref_words)} keywords matched ({hit_rate:.0%})"
    return (is_correct, reason)


# ============================================================
# 单实例评估
# ============================================================

def evaluate_answer(
    question: str,
    reference_answer: str,
    model_answer: str,
    user_id: int,
    is_abstention: bool = False,
    use_llm_judge: bool = True,
) -> Dict[str, Any]:
    """评估单个答案的正确性。

    Args:
        question: 问题
        reference_answer: 参考答案
        model_answer: 模型生成的答案
        user_id: 用户 ID（用于 LLM 调用）
        is_abstention: 是否为弃权问题
        use_llm_judge: 是否使用 LLM Judge（False 则用启发式）

    Returns:
        {
            "correct": bool,
            "evaluator": "llm_judge" | "heuristic",
            "reason": str,
        }
    """
    # 优先使用 LLM Judge
    if use_llm_judge:
        prompt = JUDGE_PROMPT_TEMPLATE.format(
            question=question[:500],
            reference_answer=reference_answer[:500],
            model_answer=model_answer[:500],
        )
        result = _call_judge_llm(prompt, user_id)
        if result is not None:
            return {
                "correct": result["correct"],
                "evaluator": "llm_judge",
                "reason": result["reason"],
            }
        logger.debug("LLM Judge 不可用，降级到启发式评判")

    # 降级到启发式评判
    correct, reason = _heuristic_judge(
        question, reference_answer, model_answer, is_abstention
    )
    return {
        "correct": correct,
        "evaluator": "heuristic",
        "reason": reason,
    }


# ============================================================
# 批量评估与指标计算
# ============================================================

def compute_metrics(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """计算评估指标。

    Args:
        results: 评估结果列表，每条包含 correct/ability/question_type 等字段

    Returns:
        {
            "total": int,
            "correct": int,
            "accuracy": float,
            "by_ability": {ability: {"total": int, "correct": int, "accuracy": float}},
            "by_question_type": {qt: {"total": int, "correct": int, "accuracy": float}},
            "by_evaluator": {evaluator: {"total": int, "correct": int, "accuracy": float}},
        }
    """
    metrics: Dict[str, Any] = {
        "total": len(results),
        "correct": sum(1 for r in results if r.get("correct")),
        "accuracy": 0.0,
        "by_ability": {},
        "by_question_type": {},
        "by_evaluator": {},
    }
    metrics["accuracy"] = (
        metrics["correct"] / metrics["total"] if metrics["total"] else 0.0
    )

    for r in results:
        # 按能力分类
        ability = r.get("ability", "unknown")
        if ability not in metrics["by_ability"]:
            metrics["by_ability"][ability] = {"total": 0, "correct": 0, "accuracy": 0.0}
        metrics["by_ability"][ability]["total"] += 1
        if r.get("correct"):
            metrics["by_ability"][ability]["correct"] += 1

        # 按问题类型分类
        qt = r.get("question_type", "unknown")
        if qt not in metrics["by_question_type"]:
            metrics["by_question_type"][qt] = {"total": 0, "correct": 0, "accuracy": 0.0}
        metrics["by_question_type"][qt]["total"] += 1
        if r.get("correct"):
            metrics["by_question_type"][qt]["correct"] += 1

        # 按评估器分类
        evaluator = r.get("evaluator", "unknown")
        if evaluator not in metrics["by_evaluator"]:
            metrics["by_evaluator"][evaluator] = {"total": 0, "correct": 0, "accuracy": 0.0}
        metrics["by_evaluator"][evaluator]["total"] += 1
        if r.get("correct"):
            metrics["by_evaluator"][evaluator]["correct"] += 1

    # 计算各类别准确率
    for category_dict in [metrics["by_ability"], metrics["by_question_type"], metrics["by_evaluator"]]:
        for key, vals in category_dict.items():
            vals["accuracy"] = (
                vals["correct"] / vals["total"] if vals["total"] else 0.0
            )

    return metrics
