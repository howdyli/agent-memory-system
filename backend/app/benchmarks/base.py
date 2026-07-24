"""
评测套件基类

定义所有评测套件的统一接口，便于 CLI 统一调度与报告生成。

每个套件需实现：
    - setup():    初始化评测环境（创建 workspace、摄入数据等）
    - run():      执行评测，返回原始结果
    - evaluate(): 计算指标，返回 BenchmarkResult

套件通过 @register_suite 装饰器自注册到 SUITE_REGISTRY。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Type


# ============================================================
# 评测结果数据类
# ============================================================

@dataclass
class BenchmarkResult:
    """单次评测套件运行的结果。

    Attributes:
        suite_name: 套件名称，如 "recall_quality"
        level: 层级，"L1" | "L2" | "L3"
        metrics: 指标字典，如 {"precision_at_5": 0.85}
        targets: 目标字典，如 {"precision_at_5": 0.80}
        passed: 是否通过所有目标
        details: 详细结果列表（每条用例一项）
        raw: 原始结果（可选，用于调试）
        timestamp: 运行时间戳
    """
    suite_name: str
    level: str
    metrics: Dict[str, float] = field(default_factory=dict)
    targets: Dict[str, float] = field(default_factory=dict)
    passed: bool = False
    details: List[Dict[str, Any]] = field(default_factory=list)
    raw: Optional[Dict[str, Any]] = None
    timestamp: str = ""

    def check_targets(self) -> None:
        """根据 metrics 与 targets 计算 passed 字段。

        对每个 target，检查对应 metric 是否达标（>= target）。
        若所有 target 达标，passed=True。
        """
        if not self.targets:
            self.passed = True
            return
        self.passed = all(
            self.metrics.get(name, 0.0) >= target
            for name, target in self.targets.items()
        )


# ============================================================
# 评测套件基类
# ============================================================

class BenchmarkSuite(ABC):
    """所有评测套件的基类。

    子类需设置类属性：
        name: 套件名称（唯一标识，如 "recall_quality"）
        level: 层级，"L1" | "L2" | "L3"
        requires_llm: 是否需要 LLM（影响 CI 调度）
        requires_external: 是否需要外部服务（PG/Milvus/TS SDK 等）

    子类需实现：
        setup(): 初始化环境
        run(): 执行评测
        evaluate(): 计算指标
    """

    name: str = ""
    level: str = ""
    requires_llm: bool = False
    requires_external: bool = False
    description: str = ""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}

    @abstractmethod
    def setup(self) -> None:
        """初始化评测环境（创建 workspace、摄入数据等）。"""

    @abstractmethod
    def run(self) -> Dict[str, Any]:
        """执行评测，返回原始结果字典。"""

    @abstractmethod
    def evaluate(self, raw_results: Dict[str, Any]) -> BenchmarkResult:
        """计算指标，返回 BenchmarkResult。"""

    def teardown(self) -> None:
        """清理评测数据。默认空实现，子类按需覆盖。"""

    def execute(self) -> BenchmarkResult:
        """完整执行一次评测：setup → run → evaluate → teardown。

        返回 BenchmarkResult。发生异常时确保 teardown 被调用。
        """
        try:
            self.setup()
            raw = self.run()
            result = self.evaluate(raw)
        finally:
            self.teardown()
        return result

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} name={self.name!r} level={self.level!r}>"


# ============================================================
# 套件注册表
# ============================================================

SUITE_REGISTRY: Dict[str, Type[BenchmarkSuite]] = {}


def register_suite(suite_cls: Type[BenchmarkSuite]) -> Type[BenchmarkSuite]:
    """装饰器：注册评测套件到 SUITE_REGISTRY。

    用法::

        @register_suite
        class RecallQualitySuite(BenchmarkSuite):
            name = "recall_quality"
            ...
    """
    if not suite_cls.name:
        raise ValueError(f"套件 {suite_cls.__name__} 未设置 name 属性")
    if suite_cls.name in SUITE_REGISTRY:
        raise ValueError(f"套件名称冲突: {suite_cls.name} 已注册")
    SUITE_REGISTRY[suite_cls.name] = suite_cls
    return suite_cls


def get_suite(name: str, config: Optional[Dict[str, Any]] = None) -> BenchmarkSuite:
    """按名称获取套件实例。"""
    if name not in SUITE_REGISTRY:
        raise KeyError(f"未知套件: {name}，已注册: {list(SUITE_REGISTRY.keys())}")
    return SUITE_REGISTRY[name](config=config)


def list_suites(level: Optional[str] = None) -> List[Dict[str, Any]]:
    """列出已注册套件。

    Args:
        level: 筛选层级（"L1"/"L2"/"L3"），None 表示全部

    Returns:
        套件信息列表，每项含 {name, level, requires_llm, requires_external, description}
    """
    suites = []
    for name, cls in SUITE_REGISTRY.items():
        if level and cls.level != level:
            continue
        suites.append({
            "name": name,
            "level": cls.level,
            "requires_llm": cls.requires_llm,
            "requires_external": cls.requires_external,
            "description": cls.description,
        })
    return suites
