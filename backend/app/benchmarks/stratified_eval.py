"""
LongMemEval-S 分层抽样评测脚本

按 5 类能力分层抽样，每类 10 题，共 50 题。
评测前禁用 LLM rerank 与矛盾检测以加速（不影响准确率，仅省 LLM 调用开销）。

用法:
    python -m app.benchmarks.stratified_eval \
        --data app/benchmarks/data/longmemeval_s.json \
        --user-id 9900 \
        --output results/longmemeval_stratified_50.json \
        --report results/longmemeval_stratified_50.md
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import random
import sys
from collections import defaultdict
from typing import Any, Dict, List

# 确保 backend 目录在 sys.path 中
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

logger = logging.getLogger(__name__)


# ============================================================
# 分层抽样
# ============================================================

def stratified_sample(
    instances: List[Dict[str, Any]],
    per_class: int = 10,
    seed: int = 42,
) -> List[Dict[str, Any]]:
    """按能力类别分层抽样。

    Args:
        instances: 完整数据集
        per_class: 每类抽样数
        seed: 随机种子（可复现）

    Returns:
        抽样后的实例列表（按原始顺序排列）
    """
    from app.benchmarks.longmemeval_adapter import get_ability

    # 按能力分组
    ability_to_indices: Dict[str, List[int]] = defaultdict(list)
    for i, item in enumerate(instances):
        ability = get_ability(item)
        ability_to_indices[ability].append(i)

    rng = random.Random(seed)
    sampled_indices: List[int] = []

    print(f"\n分层抽样方案（每类 {per_class} 题，seed={seed}）:")
    print("-" * 60)
    for ability in [
        "information_extraction",
        "multi_session_reasoning",
        "temporal_reasoning",
        "knowledge_update",
        "abstention",
    ]:
        indices = ability_to_indices.get(ability, [])
        n = min(per_class, len(indices))
        sampled = rng.sample(indices, n) if n < len(indices) else list(indices)
        sampled_indices.extend(sampled)
        print(f"  {ability:30s} 总数={len(indices):3d}  抽样={n}")

    print(f"  {'合计':30s} 总数={len(instances):3d}  抽样={len(sampled_indices)}")
    print("-" * 60)

    # 按原始顺序排列
    sampled_indices.sort()
    return [instances[i] for i in sampled_indices]


# ============================================================
# 性能优化：禁用 LLM rerank 与矛盾检测
# ============================================================

def disable_llm_rerank() -> None:
    """禁用 hybrid_search 的 LLM rerank，避免每题 6s 的 DeepSeek 调用。"""
    try:
        from app.services import hybrid_search_service as hss
        hss._memory_config_cache = {**hss.HYBRID_SEARCH_CONFIG, "rerank_enabled": False}
        logger.info("✓ 已禁用 LLM rerank（benchmark 模式）")
    except Exception as e:
        logger.warning(f"禁用 LLM rerank 失败: {e}")


def disable_contradiction_detection() -> None:
    """禁用矛盾检测，避免每条记忆创建时的额外查询开销。

    通过 monkey-patch 将 detect_contradiction 替换为 no-op。
    矛盾检测在 create_fragment 内部调用，每条记忆都会触发，
    benchmark 模式下不需要。
    """
    try:
        import app.services.contradiction_service as cs
        original = cs.detect_contradiction

        def _noop_detect(*args, **kwargs):
            return {"success": True, "contradictions": [], "skipped": "benchmark_mode"}

        cs.detect_contradiction = _noop_detect
        logger.info("✓ 已禁用矛盾检测（benchmark 模式）")
    except Exception as e:
        logger.warning(f"禁用矛盾检测失败: {e}")


# ============================================================
# 主入口
# ============================================================

def main(argv: List[str] = None) -> None:
    parser = argparse.ArgumentParser(description="LongMemEval 分层抽样评测")
    parser.add_argument("--data", type=str, required=True,
                        help="LongMemEval JSON 数据集路径")
    parser.add_argument("--per-class", type=int, default=10,
                        help="每类抽样数（默认 10）")
    parser.add_argument("--user-id", type=int, default=9900,
                        help="测试用户 ID")
    parser.add_argument("--workspace-id", type=int, default=None)
    parser.add_argument("--output", type=str,
                        default="results/longmemeval_stratified.json")
    parser.add_argument("--report", type=str, default="")
    parser.add_argument("--no-llm-answer", action="store_true",
                        help="禁用 LLM 答案生成")
    parser.add_argument("--no-llm-judge", action="store_true",
                        help="禁用 LLM Judge")
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--seed", type=int, default=42, help="随机种子")
    parser.add_argument("--keep-rerank", action="store_true",
                        help="保留 LLM rerank（默认禁用以加速）")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # 1. 加载数据集
    from app.benchmarks.longmemeval_adapter import load_dataset
    instances = load_dataset(args.data)
    print(f"\n加载数据集: {args.data}，共 {len(instances)} 条实例")

    # 2. 分层抽样
    sampled = stratified_sample(instances, args.per_class, args.seed)
    print(f"抽样完成: {len(sampled)} 条实例")

    # 3. 性能优化
    if not args.keep_rerank:
        disable_llm_rerank()
    disable_contradiction_detection()

    # 4. 运行评测
    from app.benchmarks.runner import run_benchmark, save_results, generate_markdown_report

    results = run_benchmark(
        instances=sampled,
        user_id=args.user_id,
        workspace_id=args.workspace_id,
        use_llm_judge=not args.no_llm_judge,
        use_llm_answer=not args.no_llm_answer,
        top_k_recall=args.top_k,
        repeat=args.repeat,
    )

    # 5. 保存结果
    save_results(results, args.output)

    # 6. 生成报告
    report = generate_markdown_report(results)
    report_path = args.report or args.output.replace(".json", ".md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"Markdown 报告已保存到 {report_path}")

    # 7. 打印摘要
    print("\n" + "=" * 60)
    print(f"LongMemEval 分层抽样评测完成")
    print(f"  实例总数: {results['metrics']['total']}")
    print(f"  正确数:   {results['metrics']['correct']}")
    print(f"  准确率:   {results['metrics']['accuracy']:.1%}")
    print(f"  耗时:     {results['metadata']['elapsed_seconds']} 秒")
    print(f"  平均/题:  {results['metadata']['elapsed_seconds'] / max(1, results['metrics']['total']):.1f} 秒")

    # 按能力分类打印
    print(f"\n按能力分类:")
    for ability, vals in sorted(results["metrics"]["by_ability"].items()):
        from app.benchmarks.longmemeval_adapter import ABILITY_LABELS
        label = ABILITY_LABELS.get(ability, ability)
        print(f"  {label} ({ability}): {vals['correct']}/{vals['total']} = {vals['accuracy']:.0%}")

    print("=" * 60)


if __name__ == "__main__":
    main()
