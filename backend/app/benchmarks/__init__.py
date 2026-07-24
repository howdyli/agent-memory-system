"""
Agent Memory System 基准测试框架

在 Agent Memory System 上运行各类评测套件，覆盖：
    - L1 内部质量基线：召回质量、SDK 等价性、后端一致性、多租户隔离、生命周期质量
    - L2 外部基准发布：LongMemEval、LoCoMo、LLM 稳定性
    - L3 性能与规模：延迟、吞吐、负载、存储增长

模块：
    - base:               BenchmarkSuite 基类与套件注册表
    - longmemeval_adapter: LongMemEval 数据集加载与实例适配
    - evaluator:          LLM Judge 评估器
    - runner:             基准测试运行器（统一 CLI）
    - sample_data:        合成样本数据集（用于离线验证）

子包：
    - quality/            L1 召回质量
    - equivalence/        L1 SDK 等价性
    - isolation/          L1 多租户隔离
    - lifecycle/          L1 生命周期质量
    - consistency/        L1 后端一致性
    - performance/        L3 性能基准
    - stability/          L2 LLM 稳定性
"""
from __future__ import annotations

# 导出基类与注册函数（避免循环导入，子包在 runner 中按需导入）
from app.benchmarks.base import (
    BenchmarkResult,
    BenchmarkSuite,
    SUITE_REGISTRY,
    get_suite,
    list_suites,
    register_suite,
)

__all__ = [
    "BenchmarkResult",
    "BenchmarkSuite",
    "SUITE_REGISTRY",
    "get_suite",
    "list_suites",
    "register_suite",
]
