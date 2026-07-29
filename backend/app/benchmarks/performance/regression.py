"""
性能回归检测

将本次基准运行结果与 baseline.json 对比，检测性能退化。

退化判定规则：
    - 延迟类指标（越低越好）：当前值 > 基线值 × (1 + 阈值%) → 退化
    - 吞吐类指标（越高越好）：当前值 < 基线值 × (1 - 阈值%) → 退化
    - 成功率类指标（越高越好）：当前值 < 基线值 × (1 - 阈值%) → 退化
    - 存储类指标（越低越好）：当前值 > 基线值 × (1 + 阈值%) → 退化

用法：
    # 在基准运行后对比
    python -m app.benchmarks.runner run --suite latency --output results/latency.json
    python -c "from app.benchmarks.performance.regression import check_regression; check_regression('results/latency.json')"
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_BASELINE_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "baseline.json")

# 指标方向：True=越高越好，False=越低越好
_METRIC_DIRECTION: Dict[str, bool] = {
    # latency（越低越好）
    "recall_p99": False, "semantic_p99": False, "hybrid_p99": False,
    "fragment_create_p99": False, "graph_neighbors_p99": False,
    "auto_recall_p99": False,
    # load_test
    "success_rate": True, "p99_ms": False,
    # throughput（越高越好）
    "fragment_throughput_ops": True, "fragment_success_rate": True,
    "table_record_throughput_ops": True,
    # storage_growth（越低越好）
    "avg_bytes_per_fragment": False,
}

# 各指标所属套件，用于查找基线
_METRIC_SUITE: Dict[str, str] = {
    "recall_p99": "latency", "semantic_p99": "latency", "hybrid_p99": "latency",
    "fragment_create_p99": "latency", "graph_neighbors_p99": "latency",
    "auto_recall_p99": "latency",
    "success_rate": "load_test", "p99_ms": "load_test",
    "fragment_throughput_ops": "throughput",
    "fragment_success_rate": "throughput",
    "table_record_throughput_ops": "throughput",
    "avg_bytes_per_fragment": "storage_growth",
}

# 默认退化阈值（%）：超过此比例标记为回归
_DEFAULT_THRESHOLD_PCT = 20.0


@dataclass
class RegressionItem:
    """单个指标的回归检测结果。"""
    metric: str
    baseline: float
    current: float
    direction: str  # "higher_better" | "lower_better"
    change_pct: float  # 正数=退化，负数=改善
    is_regression: bool
    threshold_pct: float
    note: str = ""


@dataclass
class RegressionReport:
    """回归检测汇总报告。"""
    has_regression: bool
    items: List[RegressionItem] = field(default_factory=list)
    baseline_path: str = ""
    current_timestamp: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "has_regression": self.has_regression,
            "baseline_path": self.baseline_path,
            "current_timestamp": self.current_timestamp,
            "items": [
                {
                    "metric": it.metric,
                    "baseline": it.baseline,
                    "current": it.current,
                    "direction": it.direction,
                    "change_pct": round(it.change_pct, 2),
                    "is_regression": it.is_regression,
                    "threshold_pct": it.threshold_pct,
                    "note": it.note,
                }
                for it in self.items
            ],
        }


def load_baseline(path: Optional[str] = None) -> Dict[str, Any]:
    """加载性能基线 JSON。"""
    p = path or _BASELINE_PATH
    if not os.path.exists(p):
        logger.warning(f"基线文件不存在: {p}")
        return {}
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def update_baseline(results: Dict[str, Any], path: Optional[str] = None) -> None:
    """用本次结果更新基线文件。

    Args:
        results: BenchmarkResult 列表序列化后的字典，格式为
            {"results": [{suite_name, metrics, ...}, ...]}
        path: 基线文件路径
    """
    p = path or _BASELINE_PATH
    baseline = load_baseline(p)
    if not baseline:
        baseline = {"version": "1.0", "suites": {}, "regression_thresholds": {}}

    suites = baseline.setdefault("suites", {})
    for item in results.get("results", []):
        name = item.get("suite_name", "")
        if name in _ALL_L3_SUITES:
            suites[name] = {
                "metrics": item.get("metrics", {}),
                "targets": item.get("targets", {}),
            }
    baseline["updated_at"] = datetime.now(timezone.utc).isoformat()

    with open(p, "w", encoding="utf-8") as f:
        json.dump(baseline, f, ensure_ascii=False, indent=2)
    logger.info(f"基线已更新: {p}")


def check_regression(
    current_results: Dict[str, Any],
    baseline_path: Optional[str] = None,
) -> RegressionReport:
    """对比当前结果与基线，检测回归。

    Args:
        current_results: BenchmarkResult 列表序列化后的字典
        baseline_path: 基线文件路径

    Returns:
        RegressionReport
    """
    baseline = load_baseline(baseline_path)
    baseline_suites = baseline.get("suites", {})
    thresholds = baseline.get("regression_thresholds", {})

    report = RegressionReport(
        has_regression=False,
        baseline_path=baseline_path or _BASELINE_PATH,
        current_timestamp=datetime.now(timezone.utc).isoformat(),
    )

    for item in current_results.get("results", []):
        suite_name = item.get("suite_name", "")
        if suite_name not in _ALL_L3_SUITES:
            continue
        base_suite = baseline_suites.get(suite_name, {})
        base_metrics = base_suite.get("metrics", {})
        current_metrics = item.get("metrics", {})

        for metric_name, current_val in current_metrics.items():
            base_val = base_metrics.get(metric_name, 0.0)
            direction_higher = _METRIC_DIRECTION.get(metric_name, True)
            threshold = _resolve_threshold(metric_name, suite_name, thresholds)

            # 基线为 0 时无法计算百分比，跳过
            if base_val == 0:
                report.items.append(RegressionItem(
                    metric=metric_name, baseline=base_val, current=current_val,
                    direction="higher_better" if direction_higher else "lower_better",
                    change_pct=0.0, is_regression=False,
                    threshold_pct=threshold,
                    note="基线为 0，跳过回归检测（请先更新基线）",
                ))
                continue

            item_reg = _detect_regression(
                base_val, current_val, direction_higher, threshold
            )
            ri = RegressionItem(
                metric=metric_name, baseline=base_val, current=current_val,
                direction="higher_better" if direction_higher else "lower_better",
                change_pct=item_reg["change_pct"],
                is_regression=item_reg["is_regression"],
                threshold_pct=threshold,
            )
            report.items.append(ri)
            if ri.is_regression:
                report.has_regression = True
                logger.warning(
                    f"⚠ 性能回归: {metric_name} 基线={base_val:.2f} "
                    f"当前={current_val:.2f} 变化={item_reg['change_pct']:+.1f}% "
                    f"(阈值 {threshold}%)"
                )

    return report


def _detect_regression(
    baseline: float, current: float, higher_better: bool, threshold_pct: float
) -> Dict[str, Any]:
    """检测单个指标的回归。

    Returns:
        {"change_pct": float, "is_regression": bool}
        change_pct 正数=退化，负数=改善
    """
    if higher_better:
        # 越高越好：下降 = 退化
        change_pct = (baseline - current) / baseline * 100.0
        is_regression = change_pct > threshold_pct
    else:
        # 越低越好：上升 = 退化
        change_pct = (current - baseline) / baseline * 100.0
        is_regression = change_pct > threshold_pct
    return {"change_pct": change_pct, "is_regression": is_regression}


def _resolve_threshold(
    metric: str, suite: str, thresholds: Dict[str, Any]
) -> float:
    """从基线配置解析指标的退化阈值。"""
    key_map = {
        "latency": "latency_p99_regression_pct",
        "throughput": "throughput_regression_pct",
        "load_test": "load_test_p99_regression_pct",
        "storage_growth": "storage_regression_pct",
    }
    key = key_map.get(suite, "")
    if key and key in thresholds:
        return float(thresholds[key])
    return _DEFAULT_THRESHOLD_PCT


def format_regression_report(report: RegressionReport) -> str:
    """将回归报告格式化为 Markdown 文本。"""
    lines = [
        "# 性能回归检测报告",
        "",
        f"**基线文件**: `{report.baseline_path}`",
        f"**检测时间**: {report.current_timestamp}",
        f"**是否回归**: {'⚠ 是' if report.has_regression else '✓ 否'}",
        "",
        "## 指标对比",
        "",
        "| 指标 | 基线 | 当前 | 变化 | 方向 | 阈值 | 回归 |",
        "|------|------|------|------|------|------|------|",
    ]
    for it in report.items:
        arrow = "↑" if it.change_pct > 0 else ("↓" if it.change_pct < 0 else "→")
        reg_mark = "⚠" if it.is_regression else "✓"
        direction = "越高越好" if it.direction == "higher_better" else "越低越好"
        note = f" ({it.note})" if it.note else ""
        lines.append(
            f"| {it.metric} | {it.baseline:.2f} | {it.current:.2f} | "
            f"{arrow}{abs(it.change_pct):.1f}% | {direction} | "
            f"{it.threshold_pct:.0f}% | {reg_mark} |{note}"
        )
    lines.append("")
    return "\n".join(lines)


# 所有 L3 套件名
_ALL_L3_SUITES = {"latency", "throughput", "load_test", "storage_growth"}
