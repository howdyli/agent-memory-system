"""
Benchmark 持久化基线管理（W4-F4.1 / F4.2）

统一管理所有层级（L1/L2/L3）评测套件的基线：
    - 基线存储：results/baselines/{suite_name}/v{version}.json（版本递增，入 Git）
    - 基线对比：按 PRD 回归规则分层判定
        * L1 质量指标：相对下降 > 5%  → fail（CI 红灯）
        * L2 准确率指标：绝对下降 > 2 个百分点 → fail（CI 红灯）
        * L3 性能指标：劣化 > 20% → warn（黄灯，不阻塞）
    - 报告生成：Markdown 表格（Suite/Metric/Baseline/Current/Delta/Status）

基线 JSON 格式（PRD 5.2.1）::

    {
      "version": 3,
      "suite": "recall_quality",
      "level": "L1",
      "generated_at": "2026-07-25T00:00:00+00:00",
      "environment": {"python": "3.13.x", "platform": "..."},
      "metrics": {"precision_at_5": {"value": 0.85, "unit": ""}},
      "thresholds": {"precision_at_5": {"min": 0.80, "regression_delta": 0.05}}
    }

用法::

    python -m app.benchmarks.runner run --suite recall_quality --update-baseline
    python -m app.benchmarks.runner run --suite recall_quality --check-regression
"""
from __future__ import annotations

import json
import logging
import os
import platform
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# 默认基线根目录：backend/results/baselines
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_BASELINE_ROOT = os.path.join(_BACKEND_DIR, "results", "baselines")

# 指标名启发式：包含这些关键词的指标视为"越低越好"
_LOWER_BETTER_HINTS = ("p50", "p95", "p99", "latency", "_ms", "bytes", "duration", "elapsed")

# PRD 回归规则参数
L1_RELATIVE_DROP_PCT = 5.0     # L1 质量指标相对下降阈值（%）
L2_ABSOLUTE_DROP = 0.02        # L2 准确率绝对下降阈值（2 个百分点）
L3_RELATIVE_WORSE_PCT = 20.0   # L3 性能劣化阈值（%）

# 状态常量
STATUS_OK = "ok"
STATUS_IMPROVED = "improved"
STATUS_WARN = "warn"
STATUS_FAIL = "fail"
STATUS_NO_BASELINE = "no_baseline"

_STATUS_MARK = {
    STATUS_OK: "✓",
    STATUS_IMPROVED: "⬆",
    STATUS_WARN: "🟡",
    STATUS_FAIL: "🔴",
    STATUS_NO_BASELINE: "—",
}


def metric_higher_better(metric_name: str) -> bool:
    """判断指标方向：True=越高越好。

    优先复用 L3 性能套件的方向表，其余按名称启发式判断。
    """
    try:
        from app.benchmarks.performance.regression import _METRIC_DIRECTION
        if metric_name in _METRIC_DIRECTION:
            return _METRIC_DIRECTION[metric_name]
    except ImportError:
        pass
    lowered = metric_name.lower()
    return not any(hint in lowered for hint in _LOWER_BETTER_HINTS)


# ============================================================
# 基线读写
# ============================================================

def _suite_dir(suite_name: str, root: Optional[str] = None) -> str:
    return os.path.join(root or DEFAULT_BASELINE_ROOT, suite_name)


def list_baseline_versions(suite_name: str, root: Optional[str] = None) -> List[int]:
    """列出某套件的所有基线版本号（升序）。"""
    d = _suite_dir(suite_name, root)
    if not os.path.isdir(d):
        return []
    versions = []
    for fname in os.listdir(d):
        m = re.fullmatch(r"v(\d+)\.json", fname)
        if m:
            versions.append(int(m.group(1)))
    return sorted(versions)


def save_baseline(
    result: Dict[str, Any],
    root: Optional[str] = None,
    version: Optional[int] = None,
) -> str:
    """保存一条套件结果为新基线版本。

    Args:
        result: 套件结果字典，需含 suite_name/level/metrics/targets
        root: 基线根目录（默认 results/baselines）
        version: 指定版本号（默认自动递增）

    Returns:
        基线文件路径
    """
    suite_name = result.get("suite_name", "")
    if not suite_name:
        raise ValueError("结果缺少 suite_name，无法保存基线")

    d = _suite_dir(suite_name, root)
    os.makedirs(d, exist_ok=True)

    if version is None:
        existing = list_baseline_versions(suite_name, root)
        version = (existing[-1] + 1) if existing else 1

    metrics = {
        name: {"value": value, "unit": ""}
        for name, value in (result.get("metrics") or {}).items()
    }
    thresholds = {}
    level = result.get("level", "")
    for name, target in (result.get("targets") or {}).items():
        thresholds[name] = {
            "min": target,
            "regression_delta": _default_regression_delta(level),
        }

    baseline = {
        "version": version,
        "suite": suite_name,
        "level": level,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "environment": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
        },
        "metrics": metrics,
        "thresholds": thresholds,
    }

    path = os.path.join(d, f"v{version}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(baseline, f, ensure_ascii=False, indent=2)
    logger.info(f"基线已保存: {path}")
    return path


def load_baseline(
    suite_name: str,
    version: Optional[int] = None,
    root: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """加载某套件的基线（默认最新版本）。不存在时返回 None。"""
    versions = list_baseline_versions(suite_name, root)
    if not versions:
        return None
    v = version if version is not None else versions[-1]
    if v not in versions:
        return None
    path = os.path.join(_suite_dir(suite_name, root), f"v{v}.json")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _default_regression_delta(level: str) -> float:
    """按层级返回默认回归阈值（记录在基线 thresholds 中，供人工参考）。"""
    if level == "L1":
        return L1_RELATIVE_DROP_PCT / 100.0
    if level == "L2":
        return L2_ABSOLUTE_DROP
    return L3_RELATIVE_WORSE_PCT / 100.0


# ============================================================
# 基线对比
# ============================================================

@dataclass
class ComparisonItem:
    """单个指标的基线对比结果。"""
    suite: str
    level: str
    metric: str
    baseline: float
    current: float
    delta: float        # current - baseline
    delta_pct: float    # 相对变化 %（基线为 0 时为 0）
    direction: str      # "higher_better" | "lower_better"
    status: str         # ok | improved | warn | fail | no_baseline
    rule: str = ""      # 触发规则说明


@dataclass
class ComparisonReport:
    """全部套件的基线对比汇总。"""
    items: List[ComparisonItem] = field(default_factory=list)
    generated_at: str = ""

    @property
    def has_failure(self) -> bool:
        return any(it.status == STATUS_FAIL for it in self.items)

    @property
    def has_warning(self) -> bool:
        return any(it.status == STATUS_WARN for it in self.items)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "has_failure": self.has_failure,
            "has_warning": self.has_warning,
            "items": [
                {
                    "suite": it.suite, "level": it.level, "metric": it.metric,
                    "baseline": it.baseline, "current": it.current,
                    "delta": round(it.delta, 6), "delta_pct": round(it.delta_pct, 2),
                    "direction": it.direction, "status": it.status, "rule": it.rule,
                }
                for it in self.items
            ],
        }


def _evaluate_metric(
    level: str, baseline: float, current: float, higher_better: bool
) -> tuple:
    """按 PRD 分层规则判定单个指标状态。

    Returns:
        (status, rule) 二元组
    """
    # 劣化量（正数=变差）
    if higher_better:
        worse_abs = baseline - current
    else:
        worse_abs = current - baseline
    worse_pct = (worse_abs / abs(baseline) * 100.0) if baseline != 0 else 0.0

    if worse_abs < 0:
        return STATUS_IMPROVED, ""
    if worse_abs == 0:
        return STATUS_OK, ""

    if level == "L1":
        if worse_pct > L1_RELATIVE_DROP_PCT:
            return STATUS_FAIL, f"L1 质量下降 {worse_pct:.1f}% > {L1_RELATIVE_DROP_PCT}%"
        return STATUS_OK, ""
    if level == "L2":
        if higher_better and worse_abs > L2_ABSOLUTE_DROP:
            return STATUS_FAIL, f"L2 准确率下降 {worse_abs*100:.1f}pp > {L2_ABSOLUTE_DROP*100:.0f}pp"
        if not higher_better and worse_pct > L3_RELATIVE_WORSE_PCT:
            return STATUS_WARN, f"L2 指标劣化 {worse_pct:.1f}% > {L3_RELATIVE_WORSE_PCT}%"
        return STATUS_OK, ""
    # L3 及未知层级：> 20% 黄灯
    if worse_pct > L3_RELATIVE_WORSE_PCT:
        return STATUS_WARN, f"性能劣化 {worse_pct:.1f}% > {L3_RELATIVE_WORSE_PCT}%"
    return STATUS_OK, ""


def compare_results(
    results: List[Dict[str, Any]],
    root: Optional[str] = None,
) -> ComparisonReport:
    """将本次运行结果与各套件最新基线对比。

    Args:
        results: 套件结果列表，每项含 suite_name/level/metrics
        root: 基线根目录

    Returns:
        ComparisonReport
    """
    report = ComparisonReport(generated_at=datetime.now(timezone.utc).isoformat())

    for item in results:
        suite_name = item.get("suite_name", "")
        level = item.get("level", "")
        current_metrics = item.get("metrics") or {}
        baseline = load_baseline(suite_name, root=root)
        baseline_metrics = (baseline or {}).get("metrics", {})

        for metric_name, current_val in current_metrics.items():
            higher = metric_higher_better(metric_name)
            direction = "higher_better" if higher else "lower_better"
            base_entry = baseline_metrics.get(metric_name)

            if baseline is None or base_entry is None:
                report.items.append(ComparisonItem(
                    suite=suite_name, level=level, metric=metric_name,
                    baseline=0.0, current=current_val, delta=0.0, delta_pct=0.0,
                    direction=direction, status=STATUS_NO_BASELINE,
                    rule="无基线，请先 --update-baseline",
                ))
                continue

            base_val = float(base_entry.get("value", 0.0))
            delta = current_val - base_val
            delta_pct = (delta / abs(base_val) * 100.0) if base_val != 0 else 0.0
            status, rule = _evaluate_metric(level, base_val, current_val, higher)
            report.items.append(ComparisonItem(
                suite=suite_name, level=level, metric=metric_name,
                baseline=base_val, current=current_val,
                delta=delta, delta_pct=delta_pct,
                direction=direction, status=status, rule=rule,
            ))

    # 埋点：回归检测结果（1=存在红灯回归）
    try:
        from app.core.metrics import benchmark_regression_detected
        benchmark_regression_detected.set(1 if report.has_failure else 0)
    except ImportError:
        pass

    if report.has_failure:
        logger.warning("🔴 检测到质量回归（超过 PRD 红灯阈值）")
    elif report.has_warning:
        logger.warning("🟡 检测到性能劣化（黄灯，不阻塞）")

    return report


# ============================================================
# 报告生成
# ============================================================

def generate_comparison_report(report: ComparisonReport) -> str:
    """将对比结果格式化为 Markdown 回归报告（F4.2）。"""
    if report.has_failure:
        summary = "🔴 **存在回归（红灯）** — 质量指标超过允许降幅，CI 应失败"
    elif report.has_warning:
        summary = "🟡 **存在性能劣化（黄灯）** — 建议排查，不阻塞合并"
    else:
        summary = "✅ **无回归** — 所有指标在基线允许范围内"

    lines = [
        "# Benchmark 回归报告",
        "",
        f"**生成时间**: {report.generated_at}",
        "",
        summary,
        "",
        "| Suite | Metric | Baseline | Current | Delta | Status |",
        "|-------|--------|----------|---------|-------|--------|",
    ]
    for it in report.items:
        mark = _STATUS_MARK.get(it.status, "?")
        if it.status == STATUS_NO_BASELINE:
            delta_str = "—"
            base_str = "—"
        else:
            arrow = "+" if it.delta >= 0 else ""
            delta_str = f"{arrow}{it.delta:.4f} ({arrow}{it.delta_pct:.1f}%)"
            base_str = f"{it.baseline:.4f}"
        note = f" {it.rule}" if it.rule and it.status in (STATUS_FAIL, STATUS_WARN) else ""
        lines.append(
            f"| {it.suite} ({it.level}) | {it.metric} | {base_str} | "
            f"{it.current:.4f} | {delta_str} | {mark} {it.status}{note} |"
        )
    lines.extend([
        "",
        "**回归规则**: L1 质量降幅 >5% 红灯 · L2 准确率降幅 >2pp 红灯 · L3 性能劣化 >20% 黄灯",
        "",
    ])
    return "\n".join(lines)
