# Agent Memory System — 评测系统实现计划

> **对应设计文档**：[2026-07-24-evaluation-system-design.md](./2026-07-24-evaluation-system-design.md)
> **日期**：2026-07-24
> **目标**：按设计文档的 5 个 Phase 实施完整评测体系
> **原则**：每个 Phase 独立交付、可验证；遵循现有代码风格与约定；复用现有基础设施

---

## 目录

1. [Phase 1：L1 核心套件](#phase-1l1-核心套件)
2. [Phase 2：L1 补全](#phase-2l1-补全)
3. [Phase 3：L2 基准发布](#phase-3l2-基准发布)
4. [Phase 4：L2 补全](#phase-4l2-补全)
5. [Phase 5：L3 性能](#phase-5l3-性能)
6. [贯穿任务：基础设施与文档](#贯穿任务基础设施与文档)

---

## Phase 1：L1 核心套件

**交付物**：召回质量 + SDK 等价性 + 多租户隔离 三个套件 + BenchmarkSuite 基类 + 固件 + CI 集成
**成功标准**：Precision@5 ≥ 0.80 / Recall@10 ≥ 0.90；HTTP↔Embedded 字段级等价；6 个隔离场景全通过

### 任务 1.1：BenchmarkSuite 基类与目录骨架

**目标**：建立统一接口与目录结构，为所有后续套件打基础。

**修改文件**：
- 新增 `backend/app/benchmarks/base.py` — `BenchmarkSuite` ABC 基类
- 新增 `backend/app/benchmarks/__init__.py` 扩展（套件注册表）
- 新增子包目录：`quality/`、`equivalence/`、`isolation/`、`lifecycle/`、`consistency/`、`performance/`、`stability/`（各含 `__init__.py`）
- 新增 `backend/app/benchmarks/fixtures/` 目录

**关键代码骨架**：
```python
# backend/app/benchmarks/base.py
from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional

class BenchmarkResult:
    """单次评测结果"""
    suite_name: str
    level: str  # "L1" | "L2" | "L3"
    metrics: Dict[str, float]
    targets: Dict[str, float]
    passed: bool
    details: List[Dict[str, Any]]
    raw: Optional[Dict[str, Any]] = None

class BenchmarkSuite(ABC):
    name: str
    level: str
    requires_llm: bool = False
    requires_external: bool = False

    @abstractmethod
    def setup(self, config: Dict[str, Any]) -> None: ...
    @abstractmethod
    def run(self) -> Dict[str, Any]: ...
    @abstractmethod
    def evaluate(self, raw_results: Dict[str, Any]) -> BenchmarkResult: ...
    def teardown(self) -> None: ...
```

**验证方式**：`python -c "from app.benchmarks.base import BenchmarkSuite; print('OK')"`

### 任务 1.2：统一 CLI 入口

**目标**：扩展现有 `runner.py` CLI 支持 `run --suite` / `run --level` / `report` 子命令，保持向后兼容。

**修改文件**：
- 重构 `backend/app/benchmarks/runner.py` — 保留现有 LongMemEval 入口，新增统一调度器
- 新增 `backend/app/benchmarks/registry.py` — 套件注册与发现

**CLI 行为**：
```bash
# 旧接口保持兼容
python -m app.benchmarks.runner --sample

# 新统一接口
python -m app.benchmarks.runner run --suite recall_quality
python -m app.benchmarks.runner run --level L1
python -m app.benchmarks.runner report --output results/ --format markdown
```

**实现要点**：
- `registry.py` 维护 `SUITE_REGISTRY: Dict[str, Type[BenchmarkSuite]]`，各套件通过 `@register_suite` 装饰器自注册
- 现有 LongMemEval 流程封装为 `LongMemEvalSuite(BenchmarkSuite)` 适配
- 旧参数 `--sample`/`--data` 内部转发到 `run --suite longmemeval`

**验证方式**：`python -m app.benchmarks.runner run --suite longmemeval --sample --no-llm-judge --no-llm-answer`（回归验证旧路径）

### 任务 1.3：召回质量评测套件

**目标**：实现 Precision@K / Recall@K / F1 / MRR / NDCG 指标计算，对比三条召回路径。

**新增文件**：
- `backend/app/benchmarks/quality/recall_quality.py` — `RecallQualitySuite` + `RecallQualityEvaluator`
- `backend/app/benchmarks/quality/ground_truth.py` — 加载 ground truth 固件
- `backend/app/benchmarks/fixtures/recall_ground_truth.json` — 30 条标注数据（10 复用合成 + 20 新增压力测试）

**实现要点**：
- `RecallQualityEvaluator.compute(query, ground_truth_ids, retrieved_results)` → `{precision_at_5, recall_at_10, f1_at_10, mrr, ndcg_at_10}`
- NDCG 计算：`DCG / IDCG`，其中 `DCG = sum(rel_i / log2(i+1))`
- Suite.run() 对每条 query 跑三条路径：标准召回（`frag_svc.search_fragments_by_semantic`）、P0 advanced_recall、P1 advanced_recall_v2
- Suite.evaluate() 汇总三条路径的指标对比，判定是否达标

**固件格式**：
```json
[
  {
    "question_id": "rq_001",
    "question": "用户的项目有哪些？",
    "expected_fragment_ids": ["frag_abc", "frag_def"],
    "capability": "multi_session",
    "path": "advanced_recall_v2"
  }
]
```

**验证方式**：`python -m app.benchmarks.runner run --suite recall_quality`，检查报告输出指标

### 任务 1.4：SDK 等价性评测套件

**目标**：验证 HTTP（Python）/ Embedded（Python）/ HTTP（TypeScript）三种客户端对同一操作序列返回等价结果。

**新增文件**：
- `backend/app/benchmarks/equivalence/sdk_equivalence.py` — `SdkEquivalenceSuite` + 字段级 diff 工具
- `backend/app/benchmarks/equivalence/ts_runner.js` — Node 子进程桥，调用 TypeScript SDK 输出 JSON
- `backend/app/benchmarks/fixtures/sdk_operations.json` — 操作序列固件

**实现要点**：
- `SdkEquivalenceSuite.setup()` 启动后端（HTTP 模式需要）+ 初始化 Embedded 客户端
- 三客户端对同一操作序列执行，收集响应列表
- `compare_responses(a, b)`：标量严格相等、浮点 `|a-b|<1e-6`、列表排序后比较、时间字段取日期部分
- TypeScript 路径：`subprocess.run(["node", "ts_runner.js", op_json])` → 解析 stdout JSON
- requires_external = True（需后端运行 + node 可用）

**固件格式**：
```json
[
  {"op": "remember", "args": {"key": "name", "value": "张三"}, "expect": {"success": true}},
  {"op": "recall", "args": {"query": "张三"}, "expect_field": "context"},
  {"op": "create_fragment", "args": {"content": "测试片段", "importance_score": 0.8}, "expect_field": "id"}
]
```

**验证方式**：`python -m app.benchmarks.runner run --suite sdk_equivalence`（需后端运行）

### 任务 1.5：多租户隔离评测套件

**目标**：验证 6 个隔离场景，零数据泄漏。

**新增文件**：
- `backend/app/benchmarks/isolation/multi_tenancy.py` — `MultiTenancySuite`
- `backend/app/benchmarks/fixtures/isolation_scenarios.json` — 场景定义

**实现要点**：
- setup 创建两个 workspace（W1/W2）+ 三种角色用户（owner/member/viewer）
- 直接调用 service 层函数（绕过 HTTP，聚焦数据隔离逻辑）
- 场景执行后断言期望结果（W2 查不到 W1 数据、越权返回 403）
- RBAC 场景：viewer 角色 DELETE 应返回权限错误

**验证方式**：`python -m app.benchmarks.runner run --suite multi_tenancy`

### 任务 1.6：统一报告生成器

**目标**：生成 Markdown 格式汇总报告。

**新增文件**：
- `backend/app/benchmarks/report.py` — `ReportGenerator`

**实现要点**：
- 输入：多个 `BenchmarkResult` 对象
- 输出：Markdown 文档，每个套件一节，含指标表、目标、通过状态
- 汇总章节：L1/L2/L3 通过率统计
- CLI：`python -m app.benchmarks.runner report --output results/ --format markdown`

**验证方式**：跑完 Phase 1 三个套件后生成报告，检查格式

### 任务 1.7：CI 集成（L1 快速套件）

**目标**：每次 PR 自动运行 L1 快速套件（无 LLM、无外部依赖），< 3min。

**修改文件**：
- 新增 `.github/workflows/benchmarks-l1.yml`（或对应 CI 配置）

**步骤**：
1. Checkout 代码 + 设置 Python 3.11
2. 安装 backend 依赖
3. 运行 `python -m app.benchmarks.runner run --suite recall_quality`
4. 运行 `python -m app.benchmarks.runner run --suite sdk_equivalence`（Python HTTP vs Embedded，跳过 TS）
5. 运行 `python -m app.benchmarks.runner run --suite multi_tenancy`
6. 生成报告并上传 artifact

**验证方式**：触发一次 CI 运行，确认全绿

---

## Phase 2：L1 补全

**交付物**：后端一致性 + 生命周期质量 两个套件 + 冲突检测固件
**成功标准**：SQLite↔PG 搜索结果集相等；半衰期误差 < 1e-6；冲突检测 P ≥ 0.85 / R ≥ 0.80

### 任务 2.1：后端一致性评测套件

**新增文件**：
- `backend/app/benchmarks/consistency/backend_consistency.py` — `BackendConsistencySuite`
- `backend/app/benchmarks/fixtures/backend_operations.json` — 操作序列固件

**实现要点**：
- 通过环境变量参数化后端：`DATABASE_URL`、`VECTOR_BACKEND`、`CACHE_BACKEND`
- run() 内部对同一固件运行两遍（后端 A / 后端 B），收集最终状态快照
- 状态快照：变量集合、片段集合、表记录、图实体
- diff 快照：搜索结果集相等（顺序可不同），向量相似度容差 1e-4
- requires_external = True（需 PG/Milvus 可用，CI 中 nightly 运行）
- LLM 固定为 mock 模式

**验证方式**：本地用 SQLite+ChromaDB vs SQLite+FakeRedis 跑一次（降级验证），完整对比在 nightly

### 任务 2.2：生命周期质量评测套件

**新增文件**：
- `backend/app/benchmarks/lifecycle/lifecycle_quality.py` — `LifecycleQualitySuite`
- `backend/app/benchmarks/lifecycle/conflict_fixtures.py` — 加载冲突对固件
- `backend/app/benchmarks/fixtures/conflict_pairs.json` — 50 对冲突检测固件

**实现要点**：
- 半衰期测试：用 `freezegun` 模拟时间流逝（1天/30天/90天），验证 decay_score 与公式 `2^(-days/half_life)` 误差 < 1e-6
- 遗忘精度：创建 20 高重要性 + 30 低重要性片段，调 `recalculate_importance`，断言高重要性误删率 < 5%
- 冲突检测：50 对固件（25 冲突 + 25 不冲突），跑 `contradiction_service`，计算 P/R
- 过期清理：创建带 TTL 片段，模拟过期，调 `cleanup_expired_memories`，断言过期 100% 清理

**固件标注**：`conflict_pairs.json` 需人工审核 25 对冲突 + 25 对不冲突，每对含 `{fragment_a, fragment_b, is_conflict, reason}`

**验证方式**：`python -m app.benchmarks.runner run --suite lifecycle_quality`

### 任务 2.3：L1 全量 CI 流程

**修改文件**：
- 更新 `.github/workflows/benchmarks-l1.yml`，增加 `lifecycle_quality` 套件
- 新增 `.github/workflows/benchmarks-nightly.yml`，含 `backend_consistency`（需 PG/Milvus service container）

**验证方式**：nightly 运行通过

---

## Phase 3：L2 基准发布

**交付物**：真实 LongMemEval-S 评测 + LLM 稳定性评测 + 对外基准报告
**成功标准**：准确率 ≥ 70%（95% Wilson CI）；10 次运行标准差 < 10%

### 任务 3.1：下载与准备真实 LongMemEval-S 数据集

**操作**：
- 下载 `longmemeval_s_cleaned.json` 到 `backend/app/benchmarks/data/`（不入版本控制，加 `.gitignore`）
- 编写 `backend/app/benchmarks/data/README.md` 说明下载方式

**验证方式**：`ls backend/app/benchmarks/data/longmemeval_s_cleaned.json`

### 任务 3.2：扩展评估器支持 Wilson CI

**修改文件**：
- `backend/app/benchmarks/evaluator.py` — 新增 `wilson_ci(correct, total, z=1.96)` 函数
- `backend/app/benchmarks/runner.py` — `compute_metrics` 增加 `accuracy_ci_low` / `accuracy_ci_high` 字段

**实现要点**：
```python
import math
def wilson_ci(correct: int, total: int, z: float = 1.96) -> tuple[float, float]:
    if total == 0: return (0.0, 0.0)
    p = correct / total
    denom = 1 + z*z / total
    center = (p + z*z / (2*total)) / denom
    spread = z * math.sqrt(p*(1-p)/total + z*z/(4*total*total)) / denom
    return (center - spread, center + spread)
```

**验证方式**：单元测试 `wilson_ci(7, 10)` ≈ (0.42, 0.90)

### 任务 3.3：多数票机制

**修改文件**：
- `backend/app/benchmarks/runner.py` — `run_benchmark` 增加 `repeat: int = 1` 参数

**实现要点**：
- 每题跑 `repeat` 次，收集 `correct` 列表
- 多数票：`sum(correct) > repeat/2` 判定为正确
- 记录每题稳定性（N 次中答对次数）
- CLI 增加 `--repeat 3` 参数

**验证方式**：`--sample --repeat 3 --no-llm-answer`（无 LLM，3 次结果应一致）

### 任务 3.4：真实 LongMemEval-S 全量评测

**操作**：
- 配置环境变量：`BENCHMARK_READER_LLM=gpt-4o-mini`、`BENCHMARK_JUDGE_LLM=gpt-4o-mini`
- 运行：`python -m app.benchmarks.runner run --suite longmemeval --data data/longmemeval_s_cleaned.json --repeat 3 --output results/longmemeval_real.json`
- 先跑 `--limit 100` 验证流程与费用，再扩到全量 500

**输出**：
- `results/longmemeval_real.json` — 原始结果
- `results/longmemeval_real.md` — Markdown 报告（含 Wilson CI、能力分类、竞品对比表）

**验证方式**：报告准确率 ≥ 70%，CI 区间合理

### 任务 3.5：LLM 稳定性评测套件

**新增文件**：
- `backend/app/benchmarks/stability/llm_stability.py` — `LlmStabilitySuite`

**实现要点**：
- 对 50 题（从合成数据抽样）跑 10 次
- 统计：准确率均值/标准差/极差、逐题稳定性分布、不稳定题列表
- 判定：标准差 < 10% 为通过

**验证方式**：`python -m app.benchmarks.runner run --suite llm_stability --repeat 10`

### 任务 3.6：更新 LongMemEval 基准报告

**修改文件**：
- `docs/LongMemEval基准测试报告.md` — 新增"真实数据集评测"章节，含 Phase 3 结果

**验证方式**：文档审阅

---

## Phase 4：L2 补全

**交付物**：LoCoMo 基准评测 + 对外基准报告
**成功标准**：准确率 ≥ 60%（95% Wilson CI）

### 任务 4.1：下载与准备 LoCoMo 数据集

**操作**：
- 从 `https://github.com/snap-research/locomo` 获取数据
- 放入 `backend/app/benchmarks/data/locomo/`（不入版本控制）

**验证方式**：数据文件就绪

### 任务 4.2：LoCoMo 适配器

**新增文件**：
- `backend/app/benchmarks/locomo_adapter.py` — 参考 `longmemeval_adapter.py` 结构

**实现要点**：
- `load_locomo_dataset(path)` → 标准化实例列表
- `LoComoAdapter` 类：ingest_history / recall_for_question / generate_answer
- 维度映射：LoCoMo 的 4 维度（提取/时间/弃权/多跳）→ 标准能力字段
- 复用 `evaluator.py` 的 LLM Judge + Heuristic

**验证方式**：`python -c "from app.benchmarks.locomo_adapter import load_locomo_dataset; print(len(load_locomo_dataset('data/locomo/')))`

### 任务 4.3：LoCoMo 评测套件

**新增文件**：
- `backend/app/benchmarks/runner.py` 注册 `LoComoSuite`

**实现要点**：
- 复用 `LongMemEvalSuite` 的多数票、Wilson CI 逻辑
- 样本量：`--limit 200`（分层抽样）或全量 1540
- 报告：总体准确率 + 4 维度分类 + 竞品对比（Zep 94.7%、Letta 74.0%）

**验证方式**：`python -m app.benchmarks.runner run --suite locomo --data data/locomo/ --limit 50 --repeat 3`

### 任务 4.4：LoCoMo 基准报告

**新增文件**：
- `docs/LoCoMo基准测试报告.md`

**验证方式**：文档审阅

---

## Phase 5：L3 性能

**交付物**：延迟 + 吞吐 + 负载 + 存储增长 四个基准 + 性能基线 + 容量规划指南
**成功标准**：召回 P99 < 500ms；100 并发错误率 < 1%

### 任务 5.1：延迟基准套件

**新增文件**：
- `backend/app/benchmarks/performance/latency.py` — `LatencyBenchmark`

**实现要点**：
- 数据规模梯度：1K / 10K / 100K 片段（setup 预生成）
- 6 类操作 × 1000 次，记录耗时，计算 P50/P95/P99
- LLM mock 模式（排除 LLM 延迟）
- 复用 Prometheus `memory_recall_latency_seconds` Histogram 辅助验证
- 目标：召回 P99 < 500ms、混合搜索 P99 < 800ms（1K 规模）

**验证方式**：`python -m app.benchmarks.runner run --suite latency --scale 1k`

### 任务 5.2：吞吐基准套件

**新增文件**：
- `backend/app/benchmarks/performance/throughput.py` — `ThroughputBenchmark`

**实现要点**：
- 三场景：10K 片段创建 / 1K 表记录导入 / 100 段会话抽取
- 并发度梯度：1 / 4 / 8 / 16
- 指标：ops/sec、总耗时、错误率、Outbox 积压量（P95 同步延迟）
- 用 `asyncio` + `httpx` 异步并发

**验证方式**：`python -m app.benchmarks.runner run --suite throughput --concurrency 8`

### 任务 5.3：负载基准套件

**新增文件**：
- `backend/app/benchmarks/performance/load_test.py` — `LoadTestRunner`

**实现要点**：
- 模拟 10/50/100 并发 workspace
- 每个 workspace 混合操作：70% 召回 + 20% 写入 + 10% 图谱查询
- 持续 10 分钟（可配置 `--duration 600`）
- 监控：错误率、P99 延迟、内存占用、SQLite 文件大小、ChromaDB 内存
- 用 `asyncio` + `httpx` 实现压测客户端

**验证方式**：`python -m app.benchmarks.runner run --suite load_test --duration 60`（先跑 1 分钟验证）

### 任务 5.4：存储增长基准套件

**新增文件**：
- `backend/app/benchmarks/performance/storage_growth.py` — `StorageGrowthBenchmark`

**实现要点**：
- 创建 1K/10K/100K 记忆，测量 SQLite/PG 文件大小、ChromaDB 目录大小、Redis 内存
- 计算每条记忆平均存储成本
- 触发 Outbox 清理 + 过期清理，测量空间回收率
- 输出存储成本表

**验证方式**：`python -m app.benchmarks.runner run --suite storage_growth`

### 任务 5.5：性能基线与回归检测

**新增文件**：
- `backend/app/benchmarks/baseline.json` — 性能基线（首次运行后生成）
- 修改 `.github/workflows/benchmarks-nightly.yml` — 增加 `latency --scale 1k`

**实现要点**：
- 首次运行 `latency --scale 1k`，结果存为 `baseline.json`
- 后续运行对比，退化 > 20% 时告警（CI 输出 warning）

**验证方式**：nightly CI 通过

### 任务 5.6：性能基准报告与容量规划指南

**新增文件**：
- `docs/性能基准报告.md` — Phase 5 结果汇总
- `docs/容量规划指南.md` — 基于存储成本与延迟数据，给出容量规划建议

**内容要点**：
- 延迟表：6 操作 × 3 规模 × P50/P95/P99
- 吞吐表：3 场景 × 4 并发度
- 负载表：3 并发数 × 错误率/P99/内存
- 存储成本表：`每 10K 记忆 ≈ X MB SQLite + Y MB ChromaDB`
- 容量规划：根据 QPS 推荐实例数、根据记忆量推荐存储配置

**验证方式**：文档审阅

---

## 贯穿任务：基础设施与文档

### 任务 X.1：评测驱动改进清单

**新增文件**：
- `docs/评测驱动改进清单.md`

**维护方式**：每轮评测完成后更新，记录发现的短板与修复状态

### 任务 X.2：README 评测章节

**修改文件**：
- `README.md` — 新增"评测"章节，列出基准评分与竞品对比
- 添加质量保证徽章（L1 套件通过状态）

**时机**：Phase 1 完成后初版，后续 Phase 完成后更新

### 任务 X.3：依赖更新

**修改文件**：
- `backend/requirements.txt` — 新增 `freezegun`（若未在 dev 依赖中）
- Phase 5 视需要新增 `locust` 或使用 `httpx` 自实现

---

## 执行顺序与依赖

```
Phase 1（任务 1.1 → 1.2 → 1.3/1.4/1.5 并行 → 1.6 → 1.7）
    ↓
Phase 2（任务 2.1/2.2 并行 → 2.3）
    ↓
Phase 3（任务 3.1 → 3.2/3.3 并行 → 3.4 → 3.5 → 3.6）
    ↓
Phase 4（任务 4.1 → 4.2 → 4.3 → 4.4）
    ↓
Phase 5（任务 5.1/5.2/5.3/5.4 可并行 → 5.5 → 5.6）
```

- Phase 1.1（基类）是所有后续套件的前置
- Phase 1.2（CLI）是所有后续套件可运行的前置
- Phase 2 依赖 Phase 1 的基类与 CLI
- Phase 3.4（真实评测）依赖 3.2（Wilson CI）与 3.3（多数票）
- Phase 4 依赖 Phase 3 的 CI 流程与报告模板
- Phase 5 依赖 Phase 1-2 的基类，但不依赖 Phase 3-4

**建议并行度**：Phase 1 内部 1.3/1.4/1.5 可三人并行；Phase 5 内部四个基准可并行。

---

## 风险应对（实施层面）

| 风险 | 应对 |
|------|------|
| ground truth 标注耗时 | Phase 1.3 先标注 10 条合成数据跑通流程，20 条压力测试数据分批补充 |
| TypeScript SDK 等价性 CI 环境复杂 | Phase 1.4 的 CI 仅跑 Python HTTP vs Embedded，TS 降级为 nightly |
| 真实 LongMemEval 摄入耗时 | Phase 3.4 先 `--limit 10` 验证单题耗时，再 `--limit 100`，最后全量 |
| LLM API 费用 | Phase 3.4 先 100 题 × 3 次 ≈ 300 次调用估算费用，确认预算后再扩量 |
| Milvus 环境搭建 | Phase 2.1 的 Milvus 对比用 docker-compose，CI 中作 optional service |
| 100K 规模延迟测试耗时 | Phase 5.1 的 100K 规模仅 nightly 运行，PR 只跑 1K |

---

*本实现计划基于设计文档 [2026-07-24-evaluation-system-design.md](./2026-07-24-evaluation-system-design.md) 制定，任务粒度可独立分配与验证。*
