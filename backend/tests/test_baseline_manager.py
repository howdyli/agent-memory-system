"""
Benchmark 基线管理测试（W4-F4.1 / F4.2）

覆盖 app/benchmarks/baseline_manager.py：
- 基线保存 / 加载 / 版本列举（目录式 results/baselines/{suite}/v{N}.json）
- 指标方向判定（higher_better 启发式）
- PRD 分层回归规则：
    * L1 质量相对下降 > 5%  → fail
    * L2 准确率绝对下降 > 2pp → fail
    * L3 性能劣化 > 20%     → warn
- Markdown 回归报告生成

全部使用 tmp_path 作为基线根目录，不触碰真实 results/baselines/。
"""
import json
import os

import pytest

from app.benchmarks.baseline_manager import (
    STATUS_FAIL,
    STATUS_IMPROVED,
    STATUS_NO_BASELINE,
    STATUS_OK,
    STATUS_WARN,
    compare_results,
    generate_comparison_report,
    list_baseline_versions,
    load_baseline,
    metric_higher_better,
    save_baseline,
)


def _suite_result(suite="recall_quality", level="L1", metrics=None, targets=None):
    """构造一条套件结果字典（runner 序列化后的格式）。"""
    return {
        "suite_name": suite,
        "level": level,
        "metrics": metrics or {"precision_at_5": 0.85},
        "targets": targets or {},
    }


# ============================================================
# 基线保存 / 加载 / 版本管理
# ============================================================

class TestBaselineReadWrite:

    def test_save_baseline_creates_v1_with_prd_format(self, tmp_path):
        """首次保存生成 v1.json，且符合 PRD 5.2.1 JSON 格式。"""
        root = str(tmp_path)
        path = save_baseline(
            _suite_result(metrics={"precision_at_5": 0.85, "mrr": 0.7},
                          targets={"precision_at_5": 0.80}),
            root=root,
        )
        assert path == os.path.join(root, "recall_quality", "v1.json")
        assert os.path.isfile(path)

        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        assert data["version"] == 1
        assert data["suite"] == "recall_quality"
        assert data["level"] == "L1"
        assert data["generated_at"]
        assert "python" in data["environment"]
        assert data["metrics"]["precision_at_5"] == {"value": 0.85, "unit": ""}
        # targets 转换为 thresholds（含层级默认 regression_delta）
        assert data["thresholds"]["precision_at_5"]["min"] == 0.80
        assert data["thresholds"]["precision_at_5"]["regression_delta"] == pytest.approx(0.05)

    def test_save_baseline_auto_increments_version(self, tmp_path):
        """连续保存版本号自动递增 v1 → v2。"""
        root = str(tmp_path)
        save_baseline(_suite_result(metrics={"m": 1.0}), root=root)
        path2 = save_baseline(_suite_result(metrics={"m": 2.0}), root=root)
        assert path2.endswith("v2.json")
        assert list_baseline_versions("recall_quality", root=root) == [1, 2]

    def test_save_baseline_missing_suite_name_raises(self, tmp_path):
        """缺少 suite_name 时抛 ValueError。"""
        with pytest.raises(ValueError):
            save_baseline({"level": "L1", "metrics": {"m": 1.0}}, root=str(tmp_path))

    def test_list_baseline_versions_sorted(self, tmp_path):
        """版本列表按数字升序（而非字典序）。"""
        root = str(tmp_path)
        for v in (3, 1, 10):
            save_baseline(_suite_result(metrics={"m": 1.0}), root=root, version=v)
        assert list_baseline_versions("recall_quality", root=root) == [1, 3, 10]

    def test_list_baseline_versions_empty_for_unknown_suite(self, tmp_path):
        """不存在的套件目录返回空列表。"""
        assert list_baseline_versions("nonexistent", root=str(tmp_path)) == []

    def test_load_baseline_returns_latest_by_default(self, tmp_path):
        """默认加载最新版本。"""
        root = str(tmp_path)
        save_baseline(_suite_result(metrics={"m": 1.0}), root=root)
        save_baseline(_suite_result(metrics={"m": 2.0}), root=root)
        data = load_baseline("recall_quality", root=root)
        assert data["version"] == 2
        assert data["metrics"]["m"]["value"] == 2.0

    def test_load_baseline_specific_version(self, tmp_path):
        """可加载指定历史版本。"""
        root = str(tmp_path)
        save_baseline(_suite_result(metrics={"m": 1.0}), root=root)
        save_baseline(_suite_result(metrics={"m": 2.0}), root=root)
        data = load_baseline("recall_quality", version=1, root=root)
        assert data["version"] == 1
        assert data["metrics"]["m"]["value"] == 1.0

    def test_load_baseline_missing_returns_none(self, tmp_path):
        """无基线或版本不存在时返回 None。"""
        root = str(tmp_path)
        assert load_baseline("recall_quality", root=root) is None
        save_baseline(_suite_result(metrics={"m": 1.0}), root=root)
        assert load_baseline("recall_quality", version=99, root=root) is None


# ============================================================
# 指标方向判定
# ============================================================

class TestMetricDirection:

    def test_quality_metric_is_higher_better(self):
        assert metric_higher_better("precision_at_5") is True
        assert metric_higher_better("compression_quality") is True

    def test_latency_metrics_are_lower_better(self):
        assert metric_higher_better("recall_p95_ms") is False
        assert metric_higher_better("injection_latency") is False
        assert metric_higher_better("context_bytes") is False


# ============================================================
# 基线对比（PRD 分层回归规则）
# ============================================================

class TestCompareResults:

    def test_no_baseline_reports_no_baseline_status(self, tmp_path):
        """无基线时状态为 no_baseline，不算失败。"""
        report = compare_results([_suite_result()], root=str(tmp_path))
        assert len(report.items) == 1
        assert report.items[0].status == STATUS_NO_BASELINE
        assert not report.has_failure
        assert not report.has_warning

    def test_l1_drop_over_5pct_fails(self, tmp_path):
        """L1 质量相对下降 > 5% → fail（红灯）。"""
        root = str(tmp_path)
        save_baseline(_suite_result(metrics={"precision_at_5": 0.80}), root=root)
        report = compare_results(
            [_suite_result(metrics={"precision_at_5": 0.70})], root=root
        )  # 下降 12.5%
        assert report.items[0].status == STATUS_FAIL
        assert report.has_failure

    def test_l1_drop_within_5pct_ok(self, tmp_path):
        """L1 下降 ≤ 5% → ok。"""
        root = str(tmp_path)
        save_baseline(_suite_result(metrics={"precision_at_5": 0.80}), root=root)
        report = compare_results(
            [_suite_result(metrics={"precision_at_5": 0.78})], root=root
        )  # 下降 2.5%
        assert report.items[0].status == STATUS_OK
        assert not report.has_failure

    def test_improvement_reports_improved(self, tmp_path):
        """指标改善 → improved。"""
        root = str(tmp_path)
        save_baseline(_suite_result(metrics={"precision_at_5": 0.80}), root=root)
        report = compare_results(
            [_suite_result(metrics={"precision_at_5": 0.90})], root=root
        )
        assert report.items[0].status == STATUS_IMPROVED

    def test_equal_value_reports_ok(self, tmp_path):
        """指标持平 → ok。"""
        root = str(tmp_path)
        save_baseline(_suite_result(metrics={"precision_at_5": 0.80}), root=root)
        report = compare_results(
            [_suite_result(metrics={"precision_at_5": 0.80})], root=root
        )
        assert report.items[0].status == STATUS_OK

    def test_l2_drop_over_2pp_fails(self, tmp_path):
        """L2 准确率绝对下降 > 2pp → fail。"""
        root = str(tmp_path)
        save_baseline(
            _suite_result(suite="locomo", level="L2", metrics={"accuracy": 0.60}),
            root=root,
        )
        report = compare_results(
            [_suite_result(suite="locomo", level="L2", metrics={"accuracy": 0.57})],
            root=root,
        )  # 下降 3pp
        assert report.items[0].status == STATUS_FAIL

    def test_l2_drop_within_2pp_ok(self, tmp_path):
        """L2 下降 ≤ 2pp → ok。"""
        root = str(tmp_path)
        save_baseline(
            _suite_result(suite="locomo", level="L2", metrics={"accuracy": 0.60}),
            root=root,
        )
        report = compare_results(
            [_suite_result(suite="locomo", level="L2", metrics={"accuracy": 0.59})],
            root=root,
        )  # 下降 1pp
        assert report.items[0].status == STATUS_OK

    def test_l3_worse_over_20pct_warns(self, tmp_path):
        """L3 性能劣化 > 20% → warn（黄灯，不算 failure）。"""
        root = str(tmp_path)
        save_baseline(
            _suite_result(suite="perf", level="L3", metrics={"recall_p95_ms": 100.0}),
            root=root,
        )
        report = compare_results(
            [_suite_result(suite="perf", level="L3", metrics={"recall_p95_ms": 130.0})],
            root=root,
        )  # 劣化 30%
        assert report.items[0].status == STATUS_WARN
        assert report.has_warning
        assert not report.has_failure

    def test_l3_worse_within_20pct_ok(self, tmp_path):
        """L3 劣化 ≤ 20% → ok。"""
        root = str(tmp_path)
        save_baseline(
            _suite_result(suite="perf", level="L3", metrics={"recall_p95_ms": 100.0}),
            root=root,
        )
        report = compare_results(
            [_suite_result(suite="perf", level="L3", metrics={"recall_p95_ms": 110.0})],
            root=root,
        )  # 劣化 10%
        assert report.items[0].status == STATUS_OK

    def test_new_metric_without_baseline_entry(self, tmp_path):
        """基线存在但缺少某指标 → 该指标 no_baseline，其余正常对比。"""
        root = str(tmp_path)
        save_baseline(_suite_result(metrics={"precision_at_5": 0.80}), root=root)
        report = compare_results(
            [_suite_result(metrics={"precision_at_5": 0.80, "new_metric": 0.5})],
            root=root,
        )
        statuses = {it.metric: it.status for it in report.items}
        assert statuses["precision_at_5"] == STATUS_OK
        assert statuses["new_metric"] == STATUS_NO_BASELINE

    def test_report_to_dict_structure(self, tmp_path):
        """to_dict 输出含汇总标志与逐指标条目。"""
        root = str(tmp_path)
        save_baseline(_suite_result(metrics={"precision_at_5": 0.80}), root=root)
        report = compare_results(
            [_suite_result(metrics={"precision_at_5": 0.70})], root=root
        )
        d = report.to_dict()
        assert d["has_failure"] is True
        assert d["items"][0]["suite"] == "recall_quality"
        assert d["items"][0]["status"] == STATUS_FAIL
        assert "delta_pct" in d["items"][0]


# ============================================================
# Markdown 回归报告
# ============================================================

class TestComparisonReport:

    def test_markdown_report_contains_table_and_rules(self, tmp_path):
        """报告包含六列表头、指标行与规则说明。"""
        root = str(tmp_path)
        save_baseline(_suite_result(metrics={"precision_at_5": 0.80}), root=root)
        report = compare_results(
            [_suite_result(metrics={"precision_at_5": 0.70})], root=root
        )
        md = generate_comparison_report(report)
        assert "| Suite | Metric | Baseline | Current | Delta | Status |" in md
        assert "recall_quality (L1)" in md
        assert "precision_at_5" in md
        assert "fail" in md
        assert "红灯" in md          # 摘要提示存在回归
        assert "回归规则" in md

    def test_markdown_report_no_regression_summary(self, tmp_path):
        """无回归时摘要为绿色通过。"""
        root = str(tmp_path)
        save_baseline(_suite_result(metrics={"precision_at_5": 0.80}), root=root)
        report = compare_results(
            [_suite_result(metrics={"precision_at_5": 0.85})], root=root
        )
        md = generate_comparison_report(report)
        assert "无回归" in md

    def test_markdown_report_no_baseline_uses_dashes(self, tmp_path):
        """无基线条目 Baseline/Delta 列显示 —。"""
        report = compare_results([_suite_result()], root=str(tmp_path))
        md = generate_comparison_report(report)
        assert "no_baseline" in md
        assert "—" in md
