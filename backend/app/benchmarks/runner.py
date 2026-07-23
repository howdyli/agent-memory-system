"""
LongMemEval 基准测试运行器

端到端运行 LongMemEval 基准测试:
    1. 加载数据集（真实或合成）
    2. 对每条实例：摄入会话 → 召回记忆 → 生成答案 → 评估正确性
    3. 计算汇总指标（总体准确率 + 分类别准确率）
    4. 输出结果 JSON 与 Markdown 报告

用法:
    # 运行合成数据集（无需下载，无需 LLM）
    python -m app.benchmarks.runner --sample

    # 运行真实 LongMemEval-S 数据集
    python -m app.benchmarks.runner --data path/to/longmemeval_s.json --limit 50

    # 指定用户 ID 和输出路径
    python -m app.benchmarks.runner --sample --user-id 999 --output results.json
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# 确保 backend 目录在 sys.path 中
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)


# ============================================================
# 基准测试运行
# ============================================================

def run_benchmark(
    instances: List[Dict[str, Any]],
    user_id: int = 999,
    workspace_id: Optional[int] = None,
    use_llm_judge: bool = True,
    use_llm_answer: bool = True,
    top_k_recall: int = 10,
    reset_memory_between: bool = True,
) -> Dict[str, Any]:
    """运行 LongMemEval 基准测试。

    Args:
        instances: 数据集实例列表
        user_id: 测试用户 ID
        workspace_id: workspace ID
        use_llm_judge: 是否使用 LLM Judge 评估（False 用启发式）
        use_llm_answer: 是否使用 LLM 生成答案（False 用启发式）
        top_k_recall: 召回记忆条数
        reset_memory_between: 是否在每条实例之间清空记忆

    Returns:
        {
            "metadata": {...},
            "results": [...],
            "metrics": {...},
            "dataset_stats": {...},
        }
    """
    from app.benchmarks.longmemeval_adapter import (
        MemoryAdapter,
        generate_answer,
        get_ability,
        dataset_stats,
    )
    from app.benchmarks.evaluator import evaluate_answer, compute_metrics

    start_time = time.time()
    results: List[Dict[str, Any]] = []

    adapter = MemoryAdapter(user_id, workspace_id)

    for i, instance in enumerate(instances):
        qid = instance.get("question_id", f"q{i}")
        question = instance.get("question", "")
        reference_answer = instance.get("answer", "")
        question_date = instance.get("question_date", "")
        question_type = instance.get("question_type", "")
        ability = get_ability(instance)
        is_abstention = qid.endswith("_abs")

        logger.info(f"[{i+1}/{len(instances)}] 评估 {qid} (ability={ability})")

        # 1. 清空记忆（避免实例间干扰）
        if reset_memory_between:
            adapter.reset()

        # 2. 摄入会话历史
        sessions = instance.get("haystack_sessions", [])
        dates = instance.get("haystack_dates", [])
        ids = instance.get("haystack_session_ids", [])
        stored = adapter.ingest_history(sessions, dates, ids)

        # 3. 召回记忆
        recalled = adapter.recall_for_question(question, top_k=top_k_recall)

        # 4. 生成答案
        answer = generate_answer(
            question=question,
            recalled_context=recalled,
            user_id=user_id,
            question_date=question_date,
        ) if use_llm_answer else _heuristic_generate(question, recalled)

        # 5. 评估答案
        eval_result = evaluate_answer(
            question=question,
            reference_answer=reference_answer,
            model_answer=answer,
            user_id=user_id,
            is_abstention=is_abstention,
            use_llm_judge=use_llm_judge,
        )

        result = {
            "question_id": qid,
            "question_type": question_type,
            "ability": ability,
            "question": question,
            "reference_answer": reference_answer,
            "model_answer": answer,
            "correct": eval_result["correct"],
            "evaluator": eval_result["evaluator"],
            "reason": eval_result["reason"],
            "stored_memories": stored,
            "recalled_length": len(recalled),
            "is_abstention": is_abstention,
        }
        results.append(result)

        status = "✓" if eval_result["correct"] else "✗"
        logger.info(f"  {status} correct={eval_result['correct']} evaluator={eval_result['evaluator']}")

    # 6. 计算汇总指标
    metrics = compute_metrics(results)
    elapsed = time.time() - start_time

    return {
        "metadata": {
            "user_id": user_id,
            "workspace_id": workspace_id,
            "total_instances": len(instances),
            "use_llm_judge": use_llm_judge,
            "use_llm_answer": use_llm_answer,
            "top_k_recall": top_k_recall,
            "reset_memory_between": reset_memory_between,
            "elapsed_seconds": round(elapsed, 2),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
        "dataset_stats": dataset_stats(instances),
        "metrics": metrics,
        "results": results,
    }


def _heuristic_generate(question: str, context: str) -> str:
    """启发式答案生成（不使用 LLM）。"""
    from app.benchmarks.longmemeval_adapter import _heuristic_answer
    return _heuristic_answer(question, context)


# ============================================================
# 结果输出
# ============================================================

def save_results(results: Dict[str, Any], output_path: str) -> None:
    """保存结果到 JSON 文件。"""
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2, default=str)
    logger.info(f"结果已保存到 {output_path}")


def generate_markdown_report(results: Dict[str, Any]) -> str:
    """生成 Markdown 格式的结果报告。"""
    meta = results["metadata"]
    metrics = results["metrics"]
    stats = results["dataset_stats"]

    lines = [
        "# LongMemEval 基准测试报告",
        "",
        f"**测试时间**: {meta['timestamp']}",
        f"**测试用户 ID**: {meta['user_id']}",
        f"**实例总数**: {meta['total_instances']}",
        f"**耗时**: {meta['elapsed_seconds']} 秒",
        f"**LLM Judge**: {'启用' if meta['use_llm_judge'] else '禁用（启发式）'}",
        f"**LLM 答案生成**: {'启用' if meta['use_llm_answer'] else '禁用（启发式）'}",
        f"**召回 top_k**: {meta['top_k_recall']}",
        "",
        "## 总体结果",
        "",
        f"| 指标 | 值 |",
        f"|------|-----|",
        f"| 总实例数 | {metrics['total']} |",
        f"| 正确数 | {metrics['correct']} |",
        f"| **准确率** | **{metrics['accuracy']:.1%}** |",
        "",
        "## 按记忆能力分类",
        "",
        "| 能力 | 总数 | 正确 | 准确率 |",
        "|------|------|------|--------|",
    ]

    for ability, vals in sorted(metrics["by_ability"].items()):
        from app.benchmarks.longmemeval_adapter import ABILITY_LABELS
        label = ABILITY_LABELS.get(ability, ability)
        lines.append(f"| {label} ({ability}) | {vals['total']} | {vals['correct']} | {vals['accuracy']:.1%} |")

    lines.extend([
        "",
        "## 按问题类型分类",
        "",
        "| 问题类型 | 总数 | 正确 | 准确率 |",
        "|----------|------|------|--------|",
    ])
    for qt, vals in sorted(metrics["by_question_type"].items()):
        lines.append(f"| {qt} | {vals['total']} | {vals['correct']} | {vals['accuracy']:.1%} |")

    lines.extend([
        "",
        "## 按评估器分类",
        "",
        "| 评估器 | 总数 | 正确 | 准确率 |",
        "|--------|------|------|--------|",
    ])
    for evaluator, vals in sorted(metrics["by_evaluator"].items()):
        lines.append(f"| {evaluator} | {vals['total']} | {vals['correct']} | {vals['accuracy']:.1%} |")

    lines.extend([
        "",
        "## 数据集统计",
        "",
        f"| 统计项 | 值 |",
        f"|--------|-----|",
        f"| 总实例数 | {stats['total']} |",
        f"| 总会话数 | {stats['total_sessions']} |",
        f"| 总轮次数 | {stats['total_turns']} |",
        f"| 用户轮次数 | {stats['total_user_turns']} |",
        f"| 平均会话数/实例 | {stats['avg_sessions_per_instance']:.1f} |",
        f"| 平均用户轮次/实例 | {stats['avg_user_turns_per_instance']:.1f} |",
        "",
        "## 详细结果",
        "",
        "| # | question_id | 能力 | 正确 | 评估器 | 问题 |",
        "|---|-------------|------|------|--------|------|",
    ])

    for i, r in enumerate(results["results"]):
        from app.benchmarks.longmemeval_adapter import ABILITY_LABELS
        label = ABILITY_LABELS.get(r["ability"], r["ability"])
        q_short = r["question"][:50] + "..." if len(r["question"]) > 50 else r["question"]
        mark = "✓" if r["correct"] else "✗"
        lines.append(f"| {i+1} | {r['question_id']} | {label} | {mark} | {r['evaluator']} | {q_short} |")

    lines.append("")
    return "\n".join(lines)


# ============================================================
# CLI 入口
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="LongMemEval 基准测试运行器")
    parser.add_argument("--sample", action="store_true", help="使用合成样本数据集")
    parser.add_argument("--data", type=str, help="真实 LongMemEval JSON 数据集路径")
    parser.add_argument("--limit", type=int, default=0, help="只评估前 N 条实例（0=全部）")
    parser.add_argument("--user-id", type=int, default=999, help="测试用户 ID")
    parser.add_argument("--workspace-id", type=int, default=None, help="workspace ID")
    parser.add_argument("--output", type=str, default="longmemeval_results.json", help="结果输出路径")
    parser.add_argument("--report", type=str, default="", help="Markdown 报告输出路径")
    parser.add_argument("--no-llm-judge", action="store_true", help="禁用 LLM Judge，使用启发式评判")
    parser.add_argument("--no-llm-answer", action="store_true", help="禁用 LLM 答案生成，使用启发式")
    parser.add_argument("--top-k", type=int, default=10, help="召回记忆条数")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # 加载数据集
    if args.sample:
        from app.benchmarks.sample_data import build_sample_dataset
        instances = build_sample_dataset()
        logger.info(f"使用合成样本数据集，共 {len(instances)} 条实例")
    elif args.data:
        from app.benchmarks.longmemeval_adapter import load_dataset
        instances = load_dataset(args.data)
    else:
        parser.error("请指定 --sample 或 --data <path>")

    # 限制实例数
    if args.limit > 0 and args.limit < len(instances):
        instances = instances[:args.limit]
        logger.info(f"限制为前 {args.limit} 条实例")

    # 运行基准测试
    results = run_benchmark(
        instances=instances,
        user_id=args.user_id,
        workspace_id=args.workspace_id,
        use_llm_judge=not args.no_llm_judge,
        use_llm_answer=not args.no_llm_answer,
        top_k_recall=args.top_k,
    )

    # 保存结果
    save_results(results, args.output)

    # 生成 Markdown 报告
    report = generate_markdown_report(results)
    report_path = args.report or args.output.replace(".json", ".md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)
    logger.info(f"Markdown 报告已保存到 {report_path}")

    # 打印摘要
    print("\n" + "=" * 60)
    print(f"LongMemEval 基准测试完成")
    print(f"  实例总数: {results['metrics']['total']}")
    print(f"  正确数:   {results['metrics']['correct']}")
    print(f"  准确率:   {results['metrics']['accuracy']:.1%}")
    print(f"  耗时:     {results['metadata']['elapsed_seconds']} 秒")
    print("=" * 60)


if __name__ == "__main__":
    main()
