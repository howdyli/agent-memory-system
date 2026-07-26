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
    repeat: int = 1,
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
        repeat: 每题重复次数（>1 时启用多数票机制，消除 LLM 非确定性）

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
    repeat = max(1, repeat)  # 至少 1 次

    adapter = MemoryAdapter(user_id, workspace_id)

    # 当不使用 LLM 生成答案时，也禁用混合检索的 LLM 重排序，避免网络超时
    _prev_rerank = None
    if not use_llm_answer:
        try:
            from app.services.hybrid_search_service import get_config, update_config
            _prev_rerank = get_config().get("rerank_enabled")
            update_config({"rerank_enabled": False})
        except Exception:
            pass

    for i, instance in enumerate(instances):
        qid = instance.get("question_id", f"q{i}")
        question = instance.get("question", "")
        reference_answer = instance.get("answer", "")
        question_date = instance.get("question_date", "")
        question_type = instance.get("question_type", "")
        ability = get_ability(instance)
        is_abstention = qid.endswith("_abs")

        logger.info(f"[{i+1}/{len(instances)}] 评估 {qid} (ability={ability}, repeat={repeat})")

        # 1. 清空记忆（避免实例间干扰）
        if reset_memory_between:
            adapter.reset()

        # 2. 摄入会话历史
        sessions = instance.get("haystack_sessions", [])
        dates = instance.get("haystack_dates", [])
        ids = instance.get("haystack_session_ids", [])
        stored = adapter.ingest_history(sessions, dates, ids)

        # 3. 多次运行收集正确性（多数票）
        run_correctness: List[bool] = []
        run_answers: List[str] = []
        run_evaluators: List[str] = []
        run_reasons: List[str] = []
        recalled_length = 0

        for run_idx in range(repeat):
            # 召回记忆
            recalled = adapter.recall_for_question(question, top_k=top_k_recall)
            recalled_length = len(recalled)

            # 生成答案
            answer = generate_answer(
                question=question,
                recalled_context=recalled,
                user_id=user_id,
                question_date=question_date,
            ) if use_llm_answer else _heuristic_generate(question, recalled)

            # 评估答案
            eval_result = evaluate_answer(
                question=question,
                reference_answer=reference_answer,
                model_answer=answer,
                user_id=user_id,
                is_abstention=is_abstention,
                use_llm_judge=use_llm_judge,
            )

            run_correctness.append(eval_result["correct"])
            run_answers.append(answer)
            run_evaluators.append(eval_result["evaluator"])
            run_reasons.append(eval_result["reason"])

        # 多数票：正确次数 > repeat/2 判定为正确
        correct_count = sum(run_correctness)
        majority_correct = correct_count > repeat / 2

        # 稳定性：N 次中答对次数（用于 LLM 稳定性分析）
        stability = correct_count / repeat

        # 选最具代表性的答案（最后一次运行）
        representative_answer = run_answers[-1]
        representative_evaluator = run_evaluators[-1]
        representative_reason = run_reasons[-1]

        result = {
            "question_id": qid,
            "question_type": question_type,
            "ability": ability,
            "question": question,
            "reference_answer": reference_answer,
            "model_answer": representative_answer,
            "correct": majority_correct,
            "evaluator": representative_evaluator,
            "reason": representative_reason,
            "stored_memories": stored,
            "recalled_length": recalled_length,
            "is_abstention": is_abstention,
            # 多数票扩展字段
            "repeat": repeat,
            "correct_count": correct_count,
            "stability": round(stability, 3),
            "run_correctness": run_correctness,
        }
        results.append(result)

        status = "✓" if majority_correct else "✗"
        stability_str = f" stability={stability:.0%}" if repeat > 1 else ""
        logger.info(f"  {status} correct={majority_correct} evaluator={representative_evaluator}{stability_str}")

    # 恢复 LLM 重排序配置
    if _prev_rerank is not None:
        try:
            from app.services.hybrid_search_service import update_config
            update_config({"rerank_enabled": _prev_rerank})
        except Exception:
            pass

    # 6. 计算汇总指标
    metrics = compute_metrics(results)
    elapsed = time.time() - start_time

    # 多数票稳定性汇总
    if repeat > 1:
        stability_scores = [r["stability"] for r in results]
        metrics["stability"] = {
            "mean": round(sum(stability_scores) / len(stability_scores), 3) if stability_scores else 0.0,
            "stable_count": sum(1 for s in stability_scores if s == 1.0),  # 100% 稳定
            "unstable_count": sum(1 for s in stability_scores if 0 < s < 1.0),  # 部分波动
            "total": len(stability_scores),
        }

    return {
        "metadata": {
            "user_id": user_id,
            "workspace_id": workspace_id,
            "total_instances": len(instances),
            "use_llm_judge": use_llm_judge,
            "use_llm_answer": use_llm_answer,
            "top_k_recall": top_k_recall,
            "reset_memory_between": reset_memory_between,
            "repeat": repeat,
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
# 统一 CLI 入口
# ============================================================

def _legacy_main(argv: Optional[List[str]] = None) -> None:
    """旧版 LongMemEval 专用 CLI（向后兼容）。

    支持 --sample / --data 等旧参数，内部转发到统一 run 子命令。
    """
    parser = argparse.ArgumentParser(description="LongMemEval 基准测试运行器（旧接口）")
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
    parser.add_argument("--repeat", type=int, default=1, help="每题重复次数（>1 启用多数票）")
    args = parser.parse_args(argv)

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
        repeat=args.repeat,
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


def _import_all_suites() -> None:
    """导入所有套件子包，触发 @register_suite 自注册。

    延迟导入避免循环依赖。新增套件模块时在此添加导入语句。
    """
    # L1 套件
    try:
        from app.benchmarks.quality import recall_quality  # noqa: F401
    except ImportError:
        pass
    try:
        from app.benchmarks.equivalence import sdk_equivalence  # noqa: F401
    except ImportError:
        pass
    try:
        from app.benchmarks.isolation import multi_tenancy  # noqa: F401
    except ImportError:
        pass
    try:
        from app.benchmarks.lifecycle import lifecycle_quality  # noqa: F401
    except ImportError:
        pass
    try:
        from app.benchmarks.consistency import backend_consistency  # noqa: F401
    except ImportError:
        pass
    # L2 套件
    try:
        from app.benchmarks import longmemeval_suite  # noqa: F401
    except ImportError:
        pass
    try:
        from app.benchmarks import locomo_suite  # noqa: F401
    except ImportError:
        pass
    try:
        from app.benchmarks.stability import llm_stability  # noqa: F401
    except ImportError:
        pass
    # L3 套件
    try:
        from app.benchmarks.performance import latency  # noqa: F401
    except ImportError:
        pass
    try:
        from app.benchmarks.performance import throughput  # noqa: F401
    except ImportError:
        pass
    try:
        from app.benchmarks.performance import load_test  # noqa: F401
    except ImportError:
        pass
    try:
        from app.benchmarks.performance import storage_growth  # noqa: F401
    except ImportError:
        pass


def _cmd_run(args: argparse.Namespace) -> int:
    """执行 `run` 子命令：运行指定套件或层级。"""
    _import_all_suites()

    from app.benchmarks.base import list_suites, get_suite

    if args.level:
        suites = list_suites(level=args.level)
        if not suites:
            print(f"层级 {args.level} 无已注册套件")
            return 1
        suite_names = [s["name"] for s in suites]
    elif args.suite:
        suite_names = [args.suite]
    elif args.all:
        suite_names = [s["name"] for s in list_suites()]
    else:
        print("请指定 --suite <name> / --level <L1|L2|L3> / --all")
        return 1

    # 构建套件配置
    config: Dict[str, Any] = {}
    if args.data:
        config["data_path"] = args.data
    if args.limit and args.limit > 0:
        config["limit"] = args.limit
    if args.repeat and args.repeat > 0:
        config["repeat"] = args.repeat
    if args.user_id:
        config["user_id"] = args.user_id
    if args.workspace_id is not None:
        config["workspace_id"] = args.workspace_id
    if args.no_llm_judge:
        config["use_llm_judge"] = False
    if args.no_llm_answer:
        config["use_llm_answer"] = False
    if args.top_k:
        config["top_k"] = args.top_k
    # L3 性能套件参数
    if getattr(args, "scale", None):
        config["scale"] = args.scale
    if getattr(args, "concurrency", None):
        config["concurrency"] = args.concurrency
    if getattr(args, "duration", 0) and args.duration > 0:
        config["duration"] = args.duration
    if getattr(args, "workspaces", None):
        config["workspaces"] = args.workspaces
    if getattr(args, "scales", None):
        config["scales"] = args.scales

    results_list = []
    exit_code = 0
    for name in suite_names:
        try:
            suite = get_suite(name, config=config)
            logger.info(f"=== 运行套件: {name} (level={suite.level}) ===")
            result = suite.execute()
            results_list.append(result)
            status = "✓ 通过" if result.passed else "✗ 未达标"
            logger.info(f"套件 {name} 完成: {status}")
            for metric_name, value in result.metrics.items():
                target = result.targets.get(metric_name)
                target_str = f" (目标 ≥{target})" if target is not None else ""
                logger.info(f"  {metric_name}: {value:.4f}{target_str}")
            if not result.passed:
                exit_code = 1
        except Exception as e:
            logger.error(f"套件 {name} 执行失败: {e}", exc_info=True)
            results_list.append(BenchmarkResult(
                suite_name=name, level="?", passed=False,
                details=[{"error": str(e)}],
            ))
            exit_code = 1

    # 保存结果
    if args.output:
        _save_suite_results(results_list, args.output)

    # 生成报告
    if args.report or args.output:
        try:
            from app.benchmarks.report import generate_combined_report
            report = generate_combined_report(results_list)
            report_path = args.report or args.output.replace(".json", ".md")
            with open(report_path, "w", encoding="utf-8") as f:
                f.write(report)
            logger.info(f"报告已保存到 {report_path}")
        except ImportError:
            logger.warning("report 模块未就绪，跳过报告生成")

    # 结果序列化（供基线管理复用）
    result_dicts = [
        {
            "suite_name": r.suite_name,
            "level": r.level,
            "metrics": r.metrics,
            "targets": r.targets,
        }
        for r in results_list
    ]

    # 更新基线（全层级：results/baselines/{suite}/v{N}.json；L3 同时维护旧版 baseline.json）
    if getattr(args, "update_baseline", False):
        try:
            from app.benchmarks.baseline_manager import save_baseline
            for item in result_dicts:
                if item["suite_name"] and item.get("metrics"):
                    save_baseline(item)
            logger.info("基线已更新（results/baselines/）")
        except Exception as e:
            logger.warning(f"更新基线失败: {e}")
        try:
            from app.benchmarks.performance.regression import update_baseline
            update_baseline({"results": result_dicts})
        except Exception as e:
            logger.warning(f"更新 L3 旧版基线失败: {e}")

    # 回归检测（全层级，未更新基线时自动对比）
    if getattr(args, "check_regression", False) and not getattr(args, "update_baseline", False):
        try:
            from app.benchmarks.baseline_manager import (
                compare_results, generate_comparison_report,
            )
            cmp_report = compare_results(result_dicts)
            md = generate_comparison_report(cmp_report)
            print("\n" + md)
            # 回归报告落盘到 results/
            regression_report_path = getattr(args, "regression_report", "") or ""
            if not regression_report_path and args.output:
                regression_report_path = args.output.replace(".json", "_regression.md")
            if regression_report_path:
                os.makedirs(os.path.dirname(regression_report_path) or ".", exist_ok=True)
                with open(regression_report_path, "w", encoding="utf-8") as f:
                    f.write(md)
                logger.info(f"回归报告已保存: {regression_report_path}")
            if cmp_report.has_failure:
                logger.warning("🔴 检测到质量回归（红灯），CI 应失败")
                exit_code = 1
            elif cmp_report.has_warning:
                logger.warning("🟡 检测到性能劣化（黄灯，不阻塞）")
        except Exception as e:
            logger.warning(f"回归检测失败: {e}")

    # 打印汇总
    print("\n" + "=" * 60)
    print("评测汇总")
    for r in results_list:
        status = "✓" if r.passed else "✗"
        print(f"  {status} {r.suite_name} ({r.level})")
    print("=" * 60)

    return exit_code


def _cmd_list(args: argparse.Namespace) -> int:
    """执行 `list` 子命令：列出已注册套件。"""
    _import_all_suites()
    from app.benchmarks.base import list_suites

    suites = list_suites(level=args.level)
    if not suites:
        print("无已注册套件")
        return 0

    print(f"{'名称':<25} {'层级':<6} {'需LLM':<8} {'需外部':<8} {'说明'}")
    print("-" * 80)
    for s in suites:
        llm = "是" if s["requires_llm"] else "否"
        ext = "是" if s["requires_external"] else "否"
        print(f"{s['name']:<25} {s['level']:<6} {llm:<8} {ext:<8} {s['description']}")
    return 0


def _cmd_report(args: argparse.Namespace) -> int:
    """执行 `report` 子命令：从已有结果文件生成报告。"""
    import os
    if not os.path.exists(args.input):
        print(f"结果文件不存在: {args.input}")
        return 1

    with open(args.input, "r", encoding="utf-8") as f:
        data = json.load(f)

    try:
        from app.benchmarks.report import generate_combined_report
        # 从加载的 JSON 重建 BenchmarkResult 列表
        results_list = []
        for item in data.get("results", []):
            results_list.append(BenchmarkResult(
                suite_name=item["suite_name"],
                level=item["level"],
                metrics=item.get("metrics", {}),
                targets=item.get("targets", {}),
                passed=item.get("passed", False),
                details=item.get("details", []),
            ))
        report = generate_combined_report(results_list)
    except ImportError:
        print("report 模块未就绪")
        return 1

    output_path = args.output or args.input.replace(".json", ".md")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"报告已生成: {output_path}")
    return 0


def _cmd_regression(args: argparse.Namespace) -> int:
    """执行 `regression` 子命令：对比结果文件与性能基线。"""
    import os
    if not os.path.exists(args.input):
        print(f"结果文件不存在: {args.input}")
        return 1

    with open(args.input, "r", encoding="utf-8") as f:
        data = json.load(f)

    try:
        from app.benchmarks.performance.regression import (
            check_regression, format_regression_report,
        )
        baseline = args.baseline if args.baseline else None
        report = check_regression(data, baseline_path=baseline)
        md = format_regression_report(report)
        print("\n" + md)
        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(md)
            print(f"回归报告已保存: {args.output}")
        return 1 if report.has_regression else 0
    except ImportError:
        print("regression 模块未就绪")
        return 1


def _cmd_baseline(args: argparse.Namespace) -> int:
    """执行 `baseline` 子命令：基于结果文件对比/更新目录式基线。"""
    if not os.path.exists(args.input):
        print(f"结果文件不存在: {args.input}")
        return 1

    with open(args.input, "r", encoding="utf-8") as f:
        data = json.load(f)
    result_dicts = data.get("results", [])

    from app.benchmarks.baseline_manager import (
        save_baseline, compare_results, generate_comparison_report,
    )

    if args.action == "update":
        for item in result_dicts:
            if item.get("suite_name") and item.get("metrics"):
                path = save_baseline(item)
                print(f"基线已保存: {path}")
        return 0

    # compare
    report = compare_results(result_dicts)
    md = generate_comparison_report(report)
    print("\n" + md)
    if args.output:
        os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(md)
        print(f"回归报告已保存: {args.output}")
    return 1 if report.has_failure else 0


def _save_suite_results(results_list: List["BenchmarkResult"], output_path: str) -> None:
    """保存套件结果列表到 JSON。"""
    from datetime import datetime, timezone

    data = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "results": [
            {
                "suite_name": r.suite_name,
                "level": r.level,
                "metrics": r.metrics,
                "targets": r.targets,
                "passed": r.passed,
                "details": r.details,
            }
            for r in results_list
        ],
    }
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)
    logger.info(f"结果已保存到 {output_path}")


def main(argv: Optional[List[str]] = None) -> None:
    """统一 CLI 入口。

    支持两种调用方式：

    1. 旧接口（向后兼容）::

        python -m app.benchmarks.runner --sample
        python -m app.benchmarks.runner --data path/to/data

    2. 新统一接口::

        python -m app.benchmarks.runner run --suite recall_quality
        python -m app.benchmarks.runner run --level L1
        python -m app.benchmarks.runner list
        python -m app.benchmarks.runner report --input results.json
    """
    # 无参数或首参以 -- 开头 → 旧接口
    raw_argv = argv if argv is not None else sys.argv[1:]
    if not raw_argv or raw_argv[0].startswith("-"):
        _legacy_main(argv)
        return

    parser = argparse.ArgumentParser(
        description="Agent Memory System 评测套件统一运行器",
        usage="python -m app.benchmarks.runner <command> [options]",
    )
    subparsers = parser.add_subparsers(dest="command", help="子命令")

    # run 子命令
    run_parser = subparsers.add_parser("run", help="运行评测套件")
    group = run_parser.add_mutually_exclusive_group()
    group.add_argument("--suite", type=str, help="套件名称")
    group.add_argument("--level", type=str, choices=["L1", "L2", "L3"], help="按层级运行")
    group.add_argument("--all", action="store_true", help="运行所有已注册套件")
    run_parser.add_argument("--data", type=str, help="数据集路径（L2 套件用）")
    run_parser.add_argument("--limit", type=int, default=0, help="限制实例数")
    run_parser.add_argument("--repeat", type=int, default=0, help="重复次数（多数票）")
    run_parser.add_argument("--user-id", type=int, default=999, help="测试用户 ID")
    run_parser.add_argument("--workspace-id", type=int, default=None, help="workspace ID")
    run_parser.add_argument("--no-llm-judge", action="store_true", help="禁用 LLM Judge")
    run_parser.add_argument("--no-llm-answer", action="store_true", help="禁用 LLM 答案生成")
    run_parser.add_argument("--top-k", type=int, default=10, help="召回记忆条数")
    run_parser.add_argument("--output", type=str, default="", help="结果输出路径")
    run_parser.add_argument("--report", type=str, default="", help="报告输出路径")
    # L3 性能套件参数
    run_parser.add_argument("--scale", type=str, default="", help="延迟套件数据规模（small/1k, medium/10k, large/100k）")
    run_parser.add_argument("--concurrency", type=str, default="", help="吞吐套件并发度（逗号分隔，如 1,4,8）")
    run_parser.add_argument("--duration", type=int, default=0, help="负载测试持续秒数")
    run_parser.add_argument("--workspaces", type=str, default="", help="负载测试并发 workspace 数（逗号分隔）")
    run_parser.add_argument("--scales", type=str, default="", help="存储增长套件规模列表（逗号分隔）")
    run_parser.add_argument("--update-baseline", action="store_true", help="运行后将结果保存为新基线版本（results/baselines/{suite}/v{N}.json）")
    run_parser.add_argument("--check-regression", action="store_true", help="运行后对比最新基线检测回归（L1>5% 红灯 / L2>2pp 红灯 / L3>20% 黄灯）")
    run_parser.add_argument("--regression-report", type=str, default="", help="回归报告输出路径（默认 <output>_regression.md）")
    run_parser.set_defaults(func=_cmd_run)

    # list 子命令
    list_parser = subparsers.add_parser("list", help="列出已注册套件")
    list_parser.add_argument("--level", type=str, choices=["L1", "L2", "L3"], help="筛选层级")
    list_parser.set_defaults(func=_cmd_list)

    # report 子命令
    report_parser = subparsers.add_parser("report", help="从结果文件生成报告")
    report_parser.add_argument("--input", type=str, required=True, help="结果 JSON 文件路径")
    report_parser.add_argument("--output", type=str, default="", help="报告输出路径")
    report_parser.set_defaults(func=_cmd_report)

    # regression 子命令：对比结果文件与基线
    reg_parser = subparsers.add_parser("regression", help="对比结果与性能基线，检测回归")
    reg_parser.add_argument("--input", type=str, required=True, help="结果 JSON 文件路径")
    reg_parser.add_argument("--baseline", type=str, default="", help="基线文件路径（默认 baseline.json）")
    reg_parser.add_argument("--output", type=str, default="", help="回归报告输出路径")
    reg_parser.set_defaults(func=_cmd_regression)

    # baseline 子命令：基于结果文件对比/更新目录式基线
    base_parser = subparsers.add_parser("baseline", help="目录式基线管理（compare/update）")
    base_parser.add_argument("action", choices=["compare", "update"], help="compare=对比最新基线, update=保存为新基线版本")
    base_parser.add_argument("--input", type=str, required=True, help="结果 JSON 文件路径（run --output 产物）")
    base_parser.add_argument("--output", type=str, default="", help="回归报告输出路径（仅 compare）")
    base_parser.set_defaults(func=_cmd_baseline)

    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if not args.command:
        parser.print_help()
        return

    exit_code = args.func(args)
    if exit_code:
        sys.exit(exit_code)


if __name__ == "__main__":
    main()
