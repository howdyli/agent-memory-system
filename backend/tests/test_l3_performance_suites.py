"""
Task 5.7: L3 性能基准套件测试

验证 L3 套件的注册、指标计算、评估逻辑与回归检测框架的正确性。
不执行真实性能采样（避免 CI 抖动），仅用合成数据测试框架逻辑。
"""
import json
import os
import tempfile
from typing import Any, Dict, List

import pytest

from app.benchmarks.base import (
    BenchmarkResult,
    SUITE_REGISTRY,
    list_suites,
    register_suite,
)
from app.benchmarks.performance._utils import (
    DEFAULT_ITERATIONS,
    PERF_SCALE,
    PERF_USER_ID,
    SCALE_ALIASES,
    SCALE_OPTIONS,
    cleanup_user_data,
    directory_size,
    file_size,
    measure,
    percentile,
    resolve_scale,
    seed_fragments,
    seed_graph,
    summarize_latency,
)


# ============================================================
# 1. 套件注册与元数据
# ============================================================

class TestL3SuiteRegistration:
    """验证 4 个 L3 套件正确注册到 SUITE_REGISTRY。"""

    @classmethod
    def setup_class(cls):
        """触发套件导入，确保注册表填充。"""
        from app.benchmarks.runner import _import_all_suites
        _import_all_suites()

    def test_all_four_l3_suites_registered(self):
        """latency / throughput / load_test / storage_growth 应全部注册。"""
        expected = {"latency", "throughput", "load_test", "storage_growth"}
        registered = {name for name, cls in SUITE_REGISTRY.items() if cls.level == "L3"}
        assert expected.issubset(registered), f"缺失 L3 套件: {expected - registered}"

    @pytest.mark.parametrize("name,description_keyword", [
        ("latency", "延迟"),
        ("throughput", "吞吐"),
        ("load_test", "负载"),
        ("storage_growth", "存储"),
    ])
    def test_suite_description_contains_keyword(self, name, description_keyword):
        """套件描述应包含对应关键词。"""
        cls = SUITE_REGISTRY.get(name)
        assert cls is not None, f"套件 {name} 未注册"
        assert description_keyword in cls.description

    @pytest.mark.parametrize("name", ["latency", "throughput", "load_test", "storage_growth"])
    def test_l3_suites_do_not_require_llm(self, name):
        """L3 性能套件不应依赖 LLM（延迟测试排除 LLM 干扰）。"""
        cls = SUITE_REGISTRY[name]
        assert cls.requires_llm is False

    @pytest.mark.parametrize("name", ["latency", "throughput", "load_test", "storage_growth"])
    def test_l3_suites_do_not_require_external(self, name):
        """L3 性能套件不应依赖外部服务（PG/Milvus）。"""
        cls = SUITE_REGISTRY[name]
        assert cls.requires_external is False

    def test_list_suites_filters_l3(self):
        """list_suites(level='L3') 应返回 4 个套件。"""
        suites = list_suites(level="L3")
        names = {s["name"] for s in suites}
        assert names == {"latency", "throughput", "load_test", "storage_growth"}

    def test_list_suites_metadata_complete(self):
        """list_suites 返回的每项应含完整字段。"""
        suites = list_suites(level="L3")
        required_fields = {"name", "level", "requires_llm", "requires_external", "description"}
        for s in suites:
            assert required_fields.issubset(s.keys())
            assert s["level"] == "L3"


class TestSuiteNameConflict:
    """验证注册表的名称冲突保护。"""

    def test_duplicate_registration_raises(self):
        """同名套件二次注册应抛 ValueError。"""
        class _Dummy:
            name = "latency"  # 已注册
            level = "L3"
            requires_llm = False
            requires_external = False
            description = "dummy"
            def setup(self): pass
            def run(self): return {}
            def evaluate(self, raw): return BenchmarkResult(suite_name="x", level="L3")

        with pytest.raises(ValueError, match="已注册"):
            register_suite(_Dummy)


# ============================================================
# 2. 共享工具：分位数与计时
# ============================================================

class TestPercentile:
    """验证分位数计算（最近秩方法）。"""

    def test_empty_samples_returns_zero(self):
        assert percentile([], 50) == 0.0

    def test_p50_of_simple_list(self):
        # [1,2,3,4,5,6,7,8,9,10] 的 P50 应在 5 附近
        samples = [float(i) for i in range(1, 11)]
        p50 = percentile(samples, 50)
        assert 4 <= p50 <= 6

    def test_p99_picks_near_max(self):
        samples = [float(i) for i in range(1, 101)]
        p99 = percentile(samples, 99)
        # 100 个样本的 P99 应接近 100
        assert p99 >= 95

    def test_p100_is_max(self):
        samples = [10.0, 20.0, 30.0, 40.0, 50.0]
        assert percentile(samples, 100) == 50.0

    def test_single_sample(self):
        assert percentile([42.0], 99) == 42.0

    def test_percentile_is_monotonic(self):
        """P50 ≤ P95 ≤ P99 ≤ max。"""
        samples = [float(i) for i in range(1, 100)]
        p50 = percentile(samples, 50)
        p95 = percentile(samples, 95)
        p99 = percentile(samples, 99)
        assert p50 <= p95 <= p99 <= max(samples)


class TestSummarizeLatency:
    """验证延迟汇总函数。"""

    def test_empty_returns_zeros(self):
        summary = summarize_latency([])
        assert summary["count"] == 0
        assert summary["p50"] == 0.0
        assert summary["p99"] == 0.0

    def test_summary_fields_complete(self):
        summary = summarize_latency([1.0, 2.0, 3.0])
        for key in ("p50", "p95", "p99", "mean", "min", "max", "count"):
            assert key in summary

    def test_mean_calculation(self):
        summary = summarize_latency([10.0, 20.0, 30.0])
        assert summary["mean"] == 20.0

    def test_min_max(self):
        summary = summarize_latency([5.0, 100.0, 50.0])
        assert summary["min"] == 5.0
        assert summary["max"] == 100.0

    def test_count_matches(self):
        summary = summarize_latency([1.0] * 42)
        assert summary["count"] == 42

    def test_values_are_rounded(self):
        """汇总值应四舍五入到 3 位小数。"""
        summary = summarize_latency([1.23456, 2.34567, 3.45678])
        # 检查最多 3 位小数
        for key in ("p50", "p95", "p99", "mean", "min", "max"):
            val = summary[key]
            assert round(val, 3) == val


class TestMeasure:
    """验证计时函数。"""

    def test_measure_returns_samples_and_returns(self):
        samples, returns = measure(lambda: 42, iterations=5)
        assert len(samples) == 5
        assert returns == [42] * 5

    def test_measure_records_errors(self):
        """函数抛异常时，应记录 _error 字典而非中断。"""
        call_count = {"n": 0}

        def flaky():
            call_count["n"] += 1
            if call_count["n"] % 2 == 0:
                raise RuntimeError("boom")
            return "ok"

        samples, returns = measure(flaky, iterations=4, warmup=0)
        assert len(samples) == 4
        assert returns[0] == "ok"
        assert isinstance(returns[1], dict) and "_error" in returns[1]

    def test_measure_warmup_not_in_samples(self):
        """预热次数不应计入 samples。"""
        counter = {"n": 0}

        def fn():
            counter["n"] += 1
            return counter["n"]

        samples, returns = measure(fn, iterations=3, warmup=5)
        assert len(samples) == 3
        # 预热 5 次后，正式采样应从 6 开始
        assert returns[0] == 6

    def test_measure_default_iterations_from_env(self):
        """DEFAULT_ITERATIONS 应能从环境变量读取。"""
        # 仅验证已被读取为整数
        assert isinstance(DEFAULT_ITERATIONS, int)
        assert DEFAULT_ITERATIONS > 0


# ============================================================
# 3. 规模解析
# ============================================================

class TestResolveScale:
    """验证规模标识解析。"""

    @pytest.mark.parametrize("key,expected", [
        ("small", 1_000),
        ("medium", 10_000),
        ("large", 100_000),
    ])
    def test_named_scales(self, key, expected):
        assert resolve_scale(key) == expected

    @pytest.mark.parametrize("alias,expected_key", [
        ("1k", "small"),
        ("10k", "medium"),
        ("100k", "large"),
        ("1000", "small"),
        ("10000", "medium"),
        ("100000", "large"),
    ])
    def test_scale_aliases(self, alias, expected_key):
        assert resolve_scale(alias) == SCALE_OPTIONS[expected_key]

    def test_case_insensitive(self):
        assert resolve_scale("SMALL") == 1_000
        assert resolve_scale("Medium") == 10_000

    def test_unknown_scale_raises(self):
        with pytest.raises(ValueError, match="未知数据规模"):
            resolve_scale("huge")

    def test_all_aliases_resolve(self):
        """所有 SCALE_ALIASES 的键都能正确解析。"""
        for alias in SCALE_ALIASES:
            assert resolve_scale(alias) > 0


# ============================================================
# 4. 文件大小工具
# ============================================================

class TestFilesizeHelpers:
    """验证文件/目录大小测量函数。"""

    def test_file_size_nonexistent_returns_zero(self, tmp_path):
        assert file_size(str(tmp_path / "nonexistent")) == 0

    def test_file_size_returns_bytes(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("hello world")
        assert file_size(str(f)) == 11  # len("hello world")

    def test_directory_size_nonexistent_returns_zero(self, tmp_path):
        assert directory_size(str(tmp_path / "nonexistent")) == 0

    def test_directory_size_sums_all_files(self, tmp_path):
        (tmp_path / "a.txt").write_text("aaaa")  # 4 bytes
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "b.txt").write_text("bbbbbb")  # 6 bytes
        assert directory_size(str(tmp_path)) == 10

    def test_directory_size_empty_returns_zero(self, tmp_path):
        assert directory_size(str(tmp_path)) == 0


# ============================================================
# 5. Latency 套件评估逻辑
# ============================================================

class TestLatencySuiteEvaluate:
    """验证 LatencyBenchmarkSuite.evaluate 的指标提取与判定。"""

    @classmethod
    def setup_class(cls):
        from app.benchmarks.runner import _import_all_suites
        _import_all_suites()

    def _make_suite(self):
        from app.benchmarks.performance.latency import LatencyBenchmarkSuite
        return LatencyBenchmarkSuite(config={"scale": "small", "iterations": 5})

    def _make_raw(self, p99_overrides: Dict[str, float] = None,
                  error_rate_override: float = 0.0) -> Dict[str, Any]:
        """构造合成 raw_results。"""
        ops = [
            {"operation": "recall", "p50": 50, "p95": 200, "p99": 300,
             "mean": 80, "error_rate": error_rate_override},
            {"operation": "semantic_search", "p50": 30, "p95": 150, "p99": 250,
             "mean": 50, "error_rate": error_rate_override},
            {"operation": "hybrid_search", "p50": 100, "p95": 400, "p99": 600,
             "mean": 150, "error_rate": error_rate_override},
            {"operation": "fragment_create", "p50": 40, "p95": 120, "p99": 180,
             "mean": 60, "error_rate": error_rate_override},
            {"operation": "graph_neighbors", "p50": 60, "p95": 200, "p99": 280,
             "mean": 90, "error_rate": error_rate_override},
            {"operation": "auto_recall", "p50": 200, "p95": 600, "p99": 900,
             "mean": 300, "error_rate": error_rate_override},
        ]
        if p99_overrides:
            for op in ops:
                if op["operation"] in p99_overrides:
                    op["p99"] = p99_overrides[op["operation"]]
        return {"scale": "small", "operations": ops}

    def test_evaluate_extracts_all_six_p99_metrics(self):
        suite = self._make_suite()
        result = suite.evaluate(self._make_raw())
        for key in ("recall_p99", "semantic_p99", "hybrid_p99",
                    "fragment_create_p99", "graph_neighbors_p99", "auto_recall_p99"):
            assert key in result.metrics
            assert result.metrics[key] > 0

    def test_evaluate_passes_when_all_targets_met(self):
        suite = self._make_suite()
        # 所有 P99 都低于目标（recall≤500, semantic≤300, hybrid≤800, create≤200, graph≤300, auto≤1000）
        result = suite.evaluate(self._make_raw())
        assert result.passed is True

    def test_evaluate_fails_when_one_p99_exceeds_target(self):
        suite = self._make_suite()
        # recall P99 = 600 > 500
        result = suite.evaluate(self._make_raw(p99_overrides={"recall": 600}))
        assert result.passed is False

    def test_evaluate_fails_when_error_rate_exceeds_threshold(self):
        """错误率 > 5% 应判未通过（即使延迟达标）。"""
        suite = self._make_suite()
        result = suite.evaluate(self._make_raw(error_rate_override=0.1))
        assert result.passed is False

    def test_evaluate_passes_with_error_rate_at_boundary(self):
        """错误率 = 5% 应判通过（边界值）。"""
        suite = self._make_suite()
        result = suite.evaluate(self._make_raw(error_rate_override=0.05))
        assert result.passed is True

    def test_evaluate_keeps_p50_p95_in_details(self):
        """P50/P95/mean 不进入 metrics（仅 P99 用于回归检测），但应保留在 details。"""
        suite = self._make_suite()
        result = suite.evaluate(self._make_raw())
        # metrics 仅含 P99（回归检测用），其中 semantic_search → semantic, hybrid_search → hybrid
        expected_metric_keys = {
            "recall_p99", "semantic_p99", "hybrid_p99",
            "fragment_create_p99", "graph_neighbors_p99", "auto_recall_p99",
        }
        assert set(result.metrics.keys()) == expected_metric_keys
        # details 保留完整分布
        details_ops = {d["operation"]: d for d in result.details}
        for op_name in ("recall", "semantic_search", "hybrid_search",
                        "fragment_create", "graph_neighbors", "auto_recall"):
            assert "p50" in details_ops[op_name]
            assert "p95" in details_ops[op_name]
            assert "mean" in details_ops[op_name]
            assert "error_rate" in details_ops[op_name]

    def test_targets_are_scaled_by_perf_scale(self):
        """延迟目标应随 PERF_SCALE 放宽。"""
        from app.benchmarks.performance.latency import TARGETS
        # TARGETS 已在模块加载时计算，验证其值 = 期望 × PERF_SCALE
        assert TARGETS["recall_p99"] == 500.0 * PERF_SCALE
        assert TARGETS["auto_recall_p99"] == 1000.0 * PERF_SCALE


# ============================================================
# 6. Throughput 套件评估逻辑
# ============================================================

class TestThroughputSuiteEvaluate:
    """验证 ThroughputBenchmarkSuite.evaluate 的指标提取与判定。"""

    @classmethod
    def setup_class(cls):
        from app.benchmarks.runner import _import_all_suites
        _import_all_suites()

    def _make_raw(self, frag_throughput=80.0, frag_error_rate=0.02,
                  table_throughput=150.0) -> Dict[str, Any]:
        """构造合成 raw_results。"""
        scenarios = [
            {"scenario": "fragment_create", "concurrency": 1,
             "throughput_ops": frag_throughput, "error_rate": frag_error_rate},
            {"scenario": "fragment_create", "concurrency": 4,
             "throughput_ops": frag_throughput * 2, "error_rate": frag_error_rate},
            {"scenario": "table_record", "concurrency": 1,
             "throughput_ops": table_throughput, "error_rate": 0.0},
            {"scenario": "conversation_extraction", "concurrency": 1,
             "skipped": True, "reason": "PERF_SKIP_LLM=1"},
        ]
        return {"scenarios": scenarios}

    def test_evaluate_extracts_c1_fragment_throughput(self):
        from app.benchmarks.performance.throughput import ThroughputBenchmarkSuite
        suite = ThroughputBenchmarkSuite()
        result = suite.evaluate(self._make_raw(frag_throughput=80.0))
        assert result.metrics["fragment_throughput_ops"] == 80.0

    def test_evaluate_extracts_c1_table_throughput(self):
        from app.benchmarks.performance.throughput import ThroughputBenchmarkSuite
        suite = ThroughputBenchmarkSuite()
        result = suite.evaluate(self._make_raw(table_throughput=150.0))
        assert result.metrics["table_record_throughput_ops"] == 150.0

    def test_evaluate_computes_success_rate_from_error_rate(self):
        from app.benchmarks.performance.throughput import ThroughputBenchmarkSuite
        suite = ThroughputBenchmarkSuite()
        result = suite.evaluate(self._make_raw(frag_error_rate=0.05))
        # success_rate = 1 - error_rate
        assert result.metrics["fragment_success_rate"] == pytest.approx(0.95)

    def test_evaluate_passes_when_all_targets_met(self):
        from app.benchmarks.performance.throughput import ThroughputBenchmarkSuite
        suite = ThroughputBenchmarkSuite()
        # fragment ≥ 50, success_rate ≥ 0.95, table ≥ 100
        result = suite.evaluate(self._make_raw(
            frag_throughput=80.0, frag_error_rate=0.02, table_throughput=150.0
        ))
        assert result.passed is True

    def test_evaluate_fails_when_fragment_throughput_below_target(self):
        from app.benchmarks.performance.throughput import ThroughputBenchmarkSuite
        suite = ThroughputBenchmarkSuite()
        result = suite.evaluate(self._make_raw(frag_throughput=30.0))
        assert result.passed is False

    def test_evaluate_fails_when_success_rate_below_target(self):
        from app.benchmarks.performance.throughput import ThroughputBenchmarkSuite
        suite = ThroughputBenchmarkSuite()
        # error_rate = 0.1 → success_rate = 0.9 < 0.95
        result = suite.evaluate(self._make_raw(frag_error_rate=0.1))
        assert result.passed is False

    def test_evaluate_fails_when_table_throughput_below_target(self):
        from app.benchmarks.performance.throughput import ThroughputBenchmarkSuite
        suite = ThroughputBenchmarkSuite()
        result = suite.evaluate(self._make_raw(table_throughput=50.0))
        assert result.passed is False


# ============================================================
# 7. Load Test 套件评估逻辑
# ============================================================

class TestLoadTestSuiteEvaluate:
    """验证 LoadTestBenchmarkSuite.evaluate 的判定逻辑。"""

    @classmethod
    def setup_class(cls):
        from app.benchmarks.runner import _import_all_suites
        _import_all_suites()

    def _make_raw(self, levels=None) -> Dict[str, Any]:
        if levels is None:
            levels = [
                {"n_workspaces": 10, "success_rate": 0.999, "p99_ms": 800.0},
                {"n_workspaces": 50, "success_rate": 0.995, "p99_ms": 1500.0},
            ]
        return {"levels": levels}

    def test_evaluate_uses_max_workspace_level(self):
        from app.benchmarks.performance.load_test import LoadTestBenchmarkSuite
        suite = LoadTestBenchmarkSuite()
        result = suite.evaluate(self._make_raw())
        # 取 50 workspace 的指标
        assert result.metrics["success_rate"] == 0.995
        assert result.metrics["p99_ms"] == 1500.0

    def test_evaluate_passes_when_targets_met(self):
        from app.benchmarks.performance.load_test import LoadTestBenchmarkSuite
        suite = LoadTestBenchmarkSuite()
        # success_rate ≥ 0.99, p99 ≤ 2000
        result = suite.evaluate(self._make_raw())
        assert result.passed is True

    def test_evaluate_fails_when_success_rate_below_target(self):
        from app.benchmarks.performance.load_test import LoadTestBenchmarkSuite
        suite = LoadTestBenchmarkSuite()
        result = suite.evaluate(self._make_raw(levels=[
            {"n_workspaces": 50, "success_rate": 0.95, "p99_ms": 1500.0},
        ]))
        assert result.passed is False

    def test_evaluate_fails_when_p99_exceeds_target(self):
        from app.benchmarks.performance.load_test import LoadTestBenchmarkSuite
        suite = LoadTestBenchmarkSuite()
        result = suite.evaluate(self._make_raw(levels=[
            {"n_workspaces": 50, "success_rate": 0.999, "p99_ms": 2500.0},
        ]))
        assert result.passed is False

    def test_evaluate_empty_levels_returns_not_passed(self):
        from app.benchmarks.performance.load_test import LoadTestBenchmarkSuite
        suite = LoadTestBenchmarkSuite()
        result = suite.evaluate({"levels": []})
        assert result.passed is False


# ============================================================
# 8. Storage Growth 套件评估逻辑
# ============================================================

class TestStorageGrowthSuiteEvaluate:
    """验证 StorageGrowthBenchmarkSuite.evaluate 的判定逻辑。"""

    @classmethod
    def setup_class(cls):
        from app.benchmarks.runner import _import_all_suites
        _import_all_suites()

    def _make_raw(self, measurements=None) -> Dict[str, Any]:
        if measurements is None:
            measurements = [
                {"scale": 1_000, "avg_bytes_per_fragment": 3000.0},
                {"scale": 10_000, "avg_bytes_per_fragment": 4500.0},
            ]
        return {"measurements": measurements}

    def test_evaluate_uses_max_scale_measurement(self):
        from app.benchmarks.performance.storage_growth import StorageGrowthBenchmarkSuite
        suite = StorageGrowthBenchmarkSuite()
        result = suite.evaluate(self._make_raw())
        # 取 10K 规模的 avg_bytes_per_fragment
        assert result.metrics["avg_bytes_per_fragment"] == 4500.0

    def test_evaluate_passes_when_below_target(self):
        from app.benchmarks.performance.storage_growth import StorageGrowthBenchmarkSuite
        suite = StorageGrowthBenchmarkSuite()
        # 目标 5000，实测 4500
        result = suite.evaluate(self._make_raw())
        assert result.passed is True

    def test_evaluate_fails_when_above_target(self):
        from app.benchmarks.performance.storage_growth import StorageGrowthBenchmarkSuite
        suite = StorageGrowthBenchmarkSuite()
        result = suite.evaluate(self._make_raw(measurements=[
            {"scale": 10_000, "avg_bytes_per_fragment": 6000.0},
        ]))
        assert result.passed is False

    def test_evaluate_empty_measurements_returns_not_passed(self):
        from app.benchmarks.performance.storage_growth import StorageGrowthBenchmarkSuite
        suite = StorageGrowthBenchmarkSuite()
        result = suite.evaluate({"measurements": []})
        assert result.passed is False

    def test_evaluate_at_boundary_passes(self):
        """avg = 5000 应判通过（≤ 目标）。"""
        from app.benchmarks.performance.storage_growth import StorageGrowthBenchmarkSuite
        suite = StorageGrowthBenchmarkSuite()
        result = suite.evaluate(self._make_raw(measurements=[
            {"scale": 10_000, "avg_bytes_per_fragment": 5000.0},
        ]))
        assert result.passed is True


# ============================================================
# 9. 回归检测
# ============================================================

class TestRegressionDetection:
    """验证 check_regression 与 _detect_regression 的判定逻辑。"""

    def test_lower_better_metric_regression_detected(self):
        """越低越好指标上升超过阈值 → 回归。"""
        from app.benchmarks.performance.regression import _detect_regression
        result = _detect_regression(baseline=100.0, current=130.0,
                                     higher_better=False, threshold_pct=20.0)
        # 上升 30% > 阈值 20%
        assert result["is_regression"] is True
        assert result["change_pct"] == pytest.approx(30.0)

    def test_lower_better_metric_no_regression_when_within_threshold(self):
        from app.benchmarks.performance.regression import _detect_regression
        result = _detect_regression(baseline=100.0, current=115.0,
                                     higher_better=False, threshold_pct=20.0)
        assert result["is_regression"] is False

    def test_higher_better_metric_regression_detected(self):
        """越高越好指标下降超过阈值 → 回归。"""
        from app.benchmarks.performance.regression import _detect_regression
        result = _detect_regression(baseline=100.0, current=70.0,
                                     higher_better=True, threshold_pct=20.0)
        # 下降 30% > 阈值 20%
        assert result["is_regression"] is True
        assert result["change_pct"] == pytest.approx(30.0)

    def test_higher_better_metric_no_regression_when_within_threshold(self):
        from app.benchmarks.performance.regression import _detect_regression
        result = _detect_regression(baseline=100.0, current=85.0,
                                     higher_better=True, threshold_pct=20.0)
        # 下降 15% < 阈值 20%
        assert result["is_regression"] is False

    def test_improvement_not_flagged_as_regression(self):
        """指标改善（变化为负）不应标记回归。"""
        from app.benchmarks.performance.regression import _detect_regression
        # 越低越好，当前更低 = 改善
        result = _detect_regression(baseline=100.0, current=50.0,
                                     higher_better=False, threshold_pct=20.0)
        assert result["is_regression"] is False
        assert result["change_pct"] < 0


class TestCheckRegression:
    """验证 check_regression 端到端对比逻辑。"""

    def _make_baseline(self, metrics: Dict[str, float]) -> Dict[str, Any]:
        return {
            "version": "1.0",
            "suites": {
                "latency": {"metrics": metrics, "targets": {}},
                "throughput": {"metrics": {}, "targets": {}},
            },
            "regression_thresholds": {
                "latency_p99_regression_pct": 20,
                "throughput_regression_pct": 20,
            },
        }

    def _make_current(self, suite_name: str, metrics: Dict[str, float]) -> Dict[str, Any]:
        return {"results": [{"suite_name": suite_name, "metrics": metrics}]}

    def test_regression_in_latency_p99_detected(self, tmp_path):
        from app.benchmarks.performance.regression import check_regression
        baseline = self._make_baseline({"recall_p99": 300.0})
        baseline_path = str(tmp_path / "baseline.json")
        with open(baseline_path, "w") as f:
            json.dump(baseline, f)

        current = self._make_current("latency", {"recall_p99": 400.0})
        report = check_regression(current, baseline_path=baseline_path)
        assert report.has_regression is True
        # 找到 recall_p99 的回归项
        item = next(it for it in report.items if it.metric == "recall_p99")
        assert item.is_regression is True
        assert item.direction == "lower_better"

    def test_no_regression_when_within_threshold(self, tmp_path):
        from app.benchmarks.performance.regression import check_regression
        baseline = self._make_baseline({"recall_p99": 300.0})
        baseline_path = str(tmp_path / "baseline.json")
        with open(baseline_path, "w") as f:
            json.dump(baseline, f)

        # 上升 10% < 阈值 20%
        current = self._make_current("latency", {"recall_p99": 330.0})
        report = check_regression(current, baseline_path=baseline_path)
        assert report.has_regression is False

    def test_baseline_zero_skipped(self, tmp_path):
        """基线为 0 时应跳过，不误报回归。"""
        from app.benchmarks.performance.regression import check_regression
        baseline = self._make_baseline({"recall_p99": 0.0})
        baseline_path = str(tmp_path / "baseline.json")
        with open(baseline_path, "w") as f:
            json.dump(baseline, f)

        current = self._make_current("latency", {"recall_p99": 1000.0})
        report = check_regression(current, baseline_path=baseline_path)
        assert report.has_regression is False
        item = next(it for it in report.items if it.metric == "recall_p99")
        assert "跳过" in item.note

    def test_missing_baseline_file_returns_empty_report(self, tmp_path):
        """基线文件不存在时不应崩溃，返回空报告。"""
        from app.benchmarks.performance.regression import check_regression
        current = self._make_current("latency", {"recall_p99": 100.0})
        report = check_regression(
            current, baseline_path=str(tmp_path / "nonexistent.json")
        )
        # 基线为空 → 所有指标基线=0 → 跳过，无回归
        assert report.has_regression is False

    def test_non_l3_suites_ignored(self, tmp_path):
        """非 L3 套件（如 recall_quality）应被跳过。"""
        from app.benchmarks.performance.regression import check_regression
        baseline = self._make_baseline({})
        baseline_path = str(tmp_path / "baseline.json")
        with open(baseline_path, "w") as f:
            json.dump(baseline, f)

        current = {"results": [
            {"suite_name": "recall_quality", "metrics": {"precision_at_5": 0.5}},
        ]}
        report = check_regression(current, baseline_path=baseline_path)
        assert report.has_regression is False
        assert len(report.items) == 0


class TestUpdateBaseline:
    """验证 update_baseline 写入逻辑。"""

    def test_update_baseline_writes_l3_metrics(self, tmp_path):
        from app.benchmarks.performance.regression import (
            load_baseline, update_baseline,
        )
        baseline_path = str(tmp_path / "baseline.json")
        # 初始空基线
        with open(baseline_path, "w") as f:
            json.dump({"version": "1.0", "suites": {}, "regression_thresholds": {}}, f)

        # 更新
        results = {"results": [
            {"suite_name": "latency",
             "metrics": {"recall_p99": 280.0},
             "targets": {"recall_p99": 500.0}},
            # 非 L3 套件应被忽略
            {"suite_name": "recall_quality",
             "metrics": {"precision_at_5": 0.85},
             "targets": {}},
        ]}
        update_baseline(results, path=baseline_path)

        baseline = load_baseline(baseline_path)
        assert "latency" in baseline["suites"]
        assert baseline["suites"]["latency"]["metrics"]["recall_p99"] == 280.0
        # recall_quality 不应被写入
        assert "recall_quality" not in baseline["suites"]
        # updated_at 应被设置
        assert baseline["updated_at"]

    def test_update_baseline_creates_file_if_missing(self, tmp_path):
        from app.benchmarks.performance.regression import (
            load_baseline, update_baseline,
        )
        baseline_path = str(tmp_path / "new_baseline.json")
        results = {"results": [
            {"suite_name": "throughput",
             "metrics": {"fragment_throughput_ops": 80.0},
             "targets": {}},
        ]}
        update_baseline(results, path=baseline_path)
        baseline = load_baseline(baseline_path)
        assert "throughput" in baseline["suites"]


class TestFormatRegressionReport:
    """验证回归报告格式化。"""

    def test_format_includes_header_and_table(self):
        from app.benchmarks.performance.regression import (
            RegressionItem, RegressionReport, format_regression_report,
        )
        report = RegressionReport(
            has_regression=True,
            baseline_path="/tmp/baseline.json",
            current_timestamp="2026-07-24T10:00:00Z",
            items=[RegressionItem(
                metric="recall_p99", baseline=300.0, current=400.0,
                direction="lower_better", change_pct=33.3,
                is_regression=True, threshold_pct=20.0,
            )],
        )
        md = format_regression_report(report)
        assert "# 性能回归检测报告" in md
        assert "⚠ 是" in md
        assert "recall_p99" in md
        assert "300.00" in md
        assert "400.00" in md

    def test_format_no_regression_shows_checkmark(self):
        from app.benchmarks.performance.regression import (
            RegressionReport, format_regression_report,
        )
        report = RegressionReport(has_regression=False)
        md = format_regression_report(report)
        assert "✓ 否" in md


# ============================================================
# 10. 配置解析（环境变量与 CLI 参数）
# ============================================================

class TestSuiteConfigParsing:
    """验证各套件从 config 与环境变量正确解析参数。"""

    @classmethod
    def setup_class(cls):
        from app.benchmarks.runner import _import_all_suites
        _import_all_suites()

    def test_latency_suite_uses_config_scale(self):
        from app.benchmarks.performance.latency import LatencyBenchmarkSuite
        suite = LatencyBenchmarkSuite(config={"scale": "medium", "iterations": 10})
        assert suite.scale == "medium"
        assert suite.target_count == 10_000
        assert suite.iterations == 10

    def test_latency_suite_defaults_to_small(self):
        from app.benchmarks.performance.latency import LatencyBenchmarkSuite
        suite = LatencyBenchmarkSuite()
        assert suite.scale == "small"
        assert suite.target_count == 1_000

    def test_latency_suite_respects_user_id_config(self):
        from app.benchmarks.performance.latency import LatencyBenchmarkSuite
        suite = LatencyBenchmarkSuite(config={"user_id": 12345})
        assert suite.user_id == 12345

    def test_throughput_suite_parses_concurrency_from_config(self):
        from app.benchmarks.performance.throughput import ThroughputBenchmarkSuite
        suite = ThroughputBenchmarkSuite(config={"concurrency": "1,8,16"})
        assert suite.concurrency_levels == [1, 8, 16]

    def test_throughput_suite_defaults_to_1_4_8(self):
        from app.benchmarks.performance.throughput import ThroughputBenchmarkSuite
        suite = ThroughputBenchmarkSuite()
        assert suite.concurrency_levels == [1, 4, 8]

    def test_load_test_suite_parses_workspaces_from_config(self):
        from app.benchmarks.performance.load_test import LoadTestBenchmarkSuite
        suite = LoadTestBenchmarkSuite(config={"workspaces": "10,100"})
        assert suite.workspace_levels == [10, 100]

    def test_load_test_suite_parses_duration_from_config(self):
        from app.benchmarks.performance.load_test import LoadTestBenchmarkSuite
        suite = LoadTestBenchmarkSuite(config={"duration": 120})
        assert suite.duration == 120

    def test_storage_growth_suite_parses_scales_from_config(self):
        from app.benchmarks.performance.storage_growth import StorageGrowthBenchmarkSuite
        suite = StorageGrowthBenchmarkSuite(config={"scales": "5000,50000"})
        assert suite.scales == [5000, 50000]

    def test_storage_growth_suite_defaults_to_1k_10k(self):
        from app.benchmarks.performance.storage_growth import StorageGrowthBenchmarkSuite
        suite = StorageGrowthBenchmarkSuite()
        assert suite.scales == [1_000, 10_000]


# ============================================================
# 11. BenchmarkResult 基类行为
# ============================================================

class TestBenchmarkResultCheckTargets:
    """验证 BenchmarkResult.check_targets 默认行为（越高越好）。"""

    def test_check_targets_passes_when_all_metrics_meet(self):
        result = BenchmarkResult(
            suite_name="test", level="L3",
            metrics={"a": 0.9, "b": 100.0},
            targets={"a": 0.8, "b": 80.0},
        )
        result.check_targets()
        assert result.passed is True

    def test_check_targets_fails_when_one_metric_misses(self):
        result = BenchmarkResult(
            suite_name="test", level="L3",
            metrics={"a": 0.7, "b": 100.0},
            targets={"a": 0.8, "b": 80.0},
        )
        result.check_targets()
        assert result.passed is False

    def test_check_targets_no_targets_passes(self):
        result = BenchmarkResult(
            suite_name="test", level="L3",
            metrics={"a": 0.1}, targets={},
        )
        result.check_targets()
        assert result.passed is True

    def test_check_targets_missing_metric_treated_as_zero(self):
        result = BenchmarkResult(
            suite_name="test", level="L3",
            metrics={}, targets={"a": 0.5},
        )
        result.check_targets()
        assert result.passed is False
