"""
统一评测报告生成器

将多个 BenchmarkResult 汇总为 Markdown 格式报告。

报告结构：
    1. 头部（日期/版本/环境）
    2. 各套件结果章节（指标表 + 目标 + 通过状态）
    3. 汇总（L1/L2/L3 通过率统计）
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List

from app.benchmarks.base import BenchmarkResult


def generate_combined_report(results: List[BenchmarkResult]) -> str:
    """生成合并的 Markdown 评测报告。

    Args:
        results: 多个套件的评测结果

    Returns:
        Markdown 格式字符串
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    lines: List[str] = [
        "# 评测报告",
        "",
        f"> 日期: {now}",
        f"> 套件数: {len(results)}",
        "",
        "---",
        "",
    ]

    # 按层级分组
    by_level: dict = {"L1": [], "L2": [], "L3": []}
    for r in results:
        level = r.level if r.level in by_level else "L1"
        by_level[level].append(r)

    # 各层级章节
    section_num = 1
    for level in ["L1", "L2", "L3"]:
        level_results = by_level[level]
        if not level_results:
            continue

        level_titles = {
            "L1": "L1 内部质量基线",
            "L2": "L2 外部基准发布",
            "L3": "L3 性能与规模",
        }
        lines.append(f"## {section_num}. {level_titles[level]}")
        lines.append("")

        for r in level_results:
            lines.extend(_format_suite_section(r))
            lines.append("")

        section_num += 1

    # 汇总
    lines.extend(_format_summary(by_level))

    return "\n".join(lines)


def _format_suite_section(r: BenchmarkResult) -> List[str]:
    """格式化单个套件的结果章节。"""
    status = "✓ 通过" if r.passed else "✗ 未达标"
    lines = [
        f"### {r.suite_name} ({r.level}) — {status}",
        "",
    ]

    # 指标表
    if r.metrics:
        lines.extend([
            "| 指标 | 值 | 目标 | 通过 |",
            "|------|-----|------|------|",
        ])
        for name, value in r.metrics.items():
            target = r.targets.get(name)
            if target is not None:
                target_str = f"≥{target}"
                metric_passed = "✓" if value >= target else "✗"
            else:
                target_str = "—"
                metric_passed = "—"
            lines.append(f"| {name} | {value:.4f} | {target_str} | {metric_passed} |")
        lines.append("")

    # 详情（限制输出量）
    if r.details:
        lines.append("<details><summary>详情</summary>")
        lines.append("")
        # 仅输出前 5 条详情，避免报告过长
        for detail in r.details[:5]:
            if isinstance(detail, dict):
                # 精简输出
                compact = {k: v for k, v in detail.items()
                          if k in ("scenario", "op", "question_id", "passed", "equal",
                                   "path", "instance_count", "error")}
                if compact:
                    lines.append(f"- `{compact}`")
                elif "error" in detail:
                    lines.append(f"- 错误: {detail['error']}")
            else:
                lines.append(f"- {detail}")
        if len(r.details) > 5:
            lines.append(f"- ... 共 {len(r.details)} 条")
        lines.append("")
        lines.append("</details>")
        lines.append("")

    return lines


def _format_summary(by_level: dict) -> List[str]:
    """格式化汇总章节。"""
    lines = [
        "---",
        "",
        "## 汇总",
        "",
        "| 层级 | 总数 | 通过 | 通过率 |",
        "|------|------|------|--------|",
    ]

    for level in ["L1", "L2", "L3"]:
        level_results = by_level[level]
        if not level_results:
            continue
        total = len(level_results)
        passed = sum(1 for r in level_results if r.passed)
        rate = passed / total if total > 0 else 0
        lines.append(f"| {level} | {total} | {passed} | {rate:.0%} |")

    # 总体
    all_results = []
    for level_results in by_level.values():
        all_results.extend(level_results)
    if all_results:
        total = len(all_results)
        passed = sum(1 for r in all_results if r.passed)
        rate = passed / total if total > 0 else 0
        lines.append(f"| **总计** | **{total}** | **{passed}** | **{rate:.0%}** |")

    lines.append("")
    return lines
