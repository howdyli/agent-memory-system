# Agent Memory System — 评测方案设计

> **日期**：2026-07-24
> **版本**：v0.3.0
> **目标**：建立完整的评测体系，保障 Agent Memory System 作为独立记忆系统的功能完备性与可靠性，并发布可对外比对的行业基准评分。
> **方案**：分层综合（方案 C）— L1 内部质量基线 + L2 外部基准发布 + L3 性能与规模

---

## 目录

1. [背景与现状](#1-背景与现状)
2. [评测维度框架](#2-评测维度框架)
3. [L1 内部质量基线（P0）](#3-l1-内部质量基线p0)
4. [L2 外部基准发布（P1）](#4-l2-外部基准发布p1)
5. [L3 性能与规模评测（P2）](#5-l3-性能与规模评测p2)
6. [评测架构与实现设计](#6-评测架构与实现设计)
7. [实施路线图与成功标准](#7-实施路线图与成功标准)

---

## 1. 背景与现状

### 1.1 项目定位

Agent Memory System（AMS）正从"前后端一体应用"演进为"可嵌入 SDK/库"的独立记忆系统（见 `docs/独立记忆系统演进方案.md`）。作为独立记忆系统，必须通过系统化评测证明：

- **功能完备且可靠**：SDK 可脱离后端独立运行、存储后端可插拔且行为一致、多租户隔离无泄漏。
- **技术可信**：拥有可对外发布的行业基准评分，在技术选型中被认真考虑。

### 1.2 现有评测能力

| 已有能力 | 说明 | 局限 |
|----------|------|------|
| LongMemEval 基准（R-02） | 10 条合成数据，LLM 全流程 100% | 仅 10 条合成数据，未跑真实 500 题数据集 |
| P0/P1 优化测试 | 80 项 P0+P1 测试 + 38 项基准框架测试 | 仅验证优化逻辑正确性，未量化召回质量 |
| 双评估器 | LLM Judge + Heuristic | LLM 非确定性导致 70%-100% 波动，未处理 |

### 1.3 明显缺口

基于竞品对标报告（`docs/竞品对标分析报告.md`）识别的评测缺口：

| 缺口 | 竞品现状 | AMS 现状 |
|------|----------|----------|
| 真实 LongMemEval-S（500 题） | Mem0 49.0%、Zep 71.2-94.7% | ❌ 仅 10 条合成数据 |
| LoCoMo 基准（1540 题） | Zep 94.7%、Letta 74.0% | ❌ 未发布 |
| 召回质量指标（P/R/F1） | 行业标准 | ❌ 仅 LLM Judge 二值准确率 |
| SDK 独立性验证 | — | ❌ HTTP vs Embedded 等价性未验证 |
| 存储后端切换一致性 | — | ❌ SQLite↔PG 等未验证 |
| 性能/延迟基准 | — | ❌ 无 P99 延迟数据 |

### 1.4 方案选型

对比三种方案后选择**方案 C：分层综合**：

| 方案 | 思路 | 优点 | 缺点 |
|------|------|------|------|
| A. 基准发布优先 | 优先跑真实数据集发布评分 | 快速建立可信度 | 无法保证 SDK/后端/多租户可靠性 |
| B. 内部质量优先 | 优先建立内部质量基线 | 确保系统可靠 | 无对外评分，可信度仍缺失 |
| **C. 分层综合（推荐）** | L1 质量基线 → L2 基准发布 → L3 性能 | 兼顾可信度与可靠性，分阶段交付 | 工作量较大，但可分期 |

---

## 2. 评测维度框架

作为独立记忆系统，评测覆盖 7 个维度：

| 维度 | 评测什么 | 为什么重要 | 所属层级 |
|------|----------|------------|----------|
| **1. 召回质量** | Precision@K / Recall@K / F1 / MRR / NDCG | 记忆系统核心能力是"找得准" | L1 |
| **2. 端到端准确率** | LongMemEval-S（500题）/ LoCoMo（1540题）准确率 | 对外技术可信度，与竞品可比对 | L2 |
| **3. SDK 独立性** | HTTP vs Embedded 结果等价性；Python SDK vs TypeScript SDK 行为对齐 | "独立记忆系统"核心诉求——SDK 可脱离后端独立运行 | L1 |
| **4. 存储后端一致性** | SQLite↔PostgreSQL、ChromaDB↔Milvus、Redis↔FakeRedis 行为对齐 | 存储可插拔是独立系统的承诺 | L1 |
| **5. 多租户隔离** | 跨 workspace 数据泄漏测试、RBAC 权限强制 | 独立系统服务多租户场景的基础安全保证 | L1 |
| **6. 生命周期与遗忘质量** | 衰减正确性、遗忘精度、冲突检测准确率 | 差异化能力（竞品大多无），需证明有效 | L1 |
| **7. 性能与规模** | 延迟 P50/P95/P99、摄入吞吐、并发负载、存储增长 | 独立系统需明确性能边界 | L3 |

---

## 3. L1 内部质量基线（P0）

最优先层级，目标是夯实"独立记忆系统"的可靠性底座。包含 5 个评测套件。

### 3.1 召回质量评测套件（Recall Quality Suite）

**目的**：用标准 IR（信息检索）指标量化召回质量，而非仅靠 LLM Judge 的二值准确率。

**数据集**：
- 复用现有 10 条合成 LongMemEval 数据，**人工标注每条问题的相关记忆片段 ID**（ground truth fragments）。
- 新增 20 条"召回压力测试"数据：多会话聚合、时间推理、知识更新各 5 条，每条标注期望召回的片段集合。

**指标与目标**：

| 指标 | 计算 | 目标 |
|------|------|------|
| Precision@5 | 前 5 结果中相关片段占比 | ≥ 0.80 |
| Recall@10 | 期望片段在前 10 中被召回比例 | ≥ 0.90 |
| F1@10 | P/R 调和平均 | ≥ 0.85 |
| MRR | 第一个相关片段的倒数排名 | ≥ 0.70 |
| NDCG@10 | 考虑排序位置的归一化折损累计增益 | ≥ 0.75 |

**实现方式**：
- 在 `backend/app/benchmarks/quality/` 新增 `recall_quality.py`。
- `RecallQualityEvaluator`：接收 `(query, ground_truth_ids, retrieved_results)`，输出指标字典。
- 复用 `longmemeval_adapter.py` 的摄入/召回流程，仅替换评判环节。
- 每次召回同时跑标准召回、P0 advanced_recall、P1 advanced_recall_v2 三条路径，输出对比。

### 3.2 SDK 模式等价性评测套件（SDK Equivalence Suite）

**目的**：验证 HTTP 模式与 Embedded 模式返回结果一致，Python SDK 与 TypeScript SDK 行为对齐。这是"SDK 可脱离后端独立运行"的核心证据。

**测试矩阵**：

| 操作 | HTTP (Python) | Embedded (Python) | HTTP (TypeScript) | 期望 |
|------|---------------|--------------------|--------------------|------|
| remember/recall | ✓ | ✓ | ✓ | 三者返回语义等价 |
| fragment create/get/search | ✓ | ✓ | ✓ | id/内容/similarity 一致 |
| table create/add_record/query | ✓ | ✓ | ✓ | 记录集合相等 |
| graph add_entity/neighbors | ✓ | ✓ | ✓ | 实体/邻居集合相等 |

**等价性判定规则**：
- 标量字段（id、count、success）：严格相等。
- 浮点字段（similarity_score、importance_score）：`|a - b| < 1e-6`。
- 列表字段：排序后逐元素比较，允许顺序差异。
- 时间字段：仅比较日期部分（嵌入式与 HTTP 可能有时区偏差）。

**实现方式**：
- `backend/app/benchmarks/equivalence/sdk_equivalence.py` 中的 `SdkEquivalenceRunner`。
- 对同一组操作序列，分别用三种客户端执行，收集响应并做字段级 diff。
- 复用 `sdk-python`（MemoryClient HTTP + Embedded）和 `sdk-typescript`（MemoryClient HTTP）。
- TypeScript 通过子进程调用 `node -e` 执行，输出 JSON 对比。

### 3.3 存储后端一致性评测套件（Backend Consistency Suite）

**目的**：验证存储后端切换后行为一致。

**测试矩阵**（2×2 笛卡尔积对比）：

| 维度 | 后端 A | 后端 B |
|------|--------|--------|
| 关系 | SQLite | PostgreSQL |
| 向量 | ChromaDB | Milvus |
| 缓存 | Redis | FakeRedis |

**一致性判定**：
- 同一操作序列在两个后端上执行，最终状态一致。
- 重点验证：FTS5（SQLite）vs ILIKE（PostgreSQL）的搜索结果集相等（顺序可不同）。
- 向量搜索：由于嵌入模型一致，top_k 结果集应相等（相似度容差 1e-4）。

**实现方式**：
- `backend/app/benchmarks/consistency/backend_consistency.py` 中的 `BackendConsistencyRunner`。
- 参数化 `DATABASE_URL`/`VECTOR_BACKEND`/`CACHE_BACKEND`，对同一测试固件运行两遍，diff 最终状态快照。
- 固定 LLM 后端为 mock 模式，排除 LLM 非确定性干扰。

### 3.4 多租户隔离评测套件（Multi-Tenancy Isolation Suite）

**目的**：证明 workspace 间数据严格隔离，RBAC 权限强制生效。

**测试场景**：

| 场景 | 操作 | 期望 |
|------|------|------|
| 变量隔离 | W1 设 `key=v1`，W2 查 `key` | W2 返回 None |
| 片段隔离 | W1 创建片段，W2 列表 | W2 列表不含 W1 片段 |
| 表隔离 | W1 建表 `t`，W2 建表 `t` | 两者独立，互不影响 |
| 图谱隔离 | W1 建实体 E，W2 查 E | W2 查不到 |
| 越权访问 | W1 的 member 角色用 W2 的 token 访问 W2 资源 | 403 Forbidden |
| RBAC 权限 | viewer 角色尝试 DELETE | 403 Forbidden |

**实现方式**：
- `backend/app/benchmarks/isolation/multi_tenancy.py` 中的 `MultiTenancyRunner`。
- 创建两个 workspace + 三种角色用户（owner/member/viewer）。
- 对每类资源跑隔离 + 越权断言。

### 3.5 生命周期与遗忘质量评测套件（Lifecycle & Forgetting Quality Suite）

**目的**：验证差异化半衰期、智能遗忘、冲突检测这三个差异化能力确实有效。

**评测项与目标**：

| 评测项 | 方法 | 目标 |
|--------|------|------|
| 半衰期衰减正确性 | 创建 info(永久)/plan(90d)/preference(1d) 各 10 条，模拟时间流逝，检查 decay_score | 衰减值与公式 `2^(-days/half_life)` 误差 < 1e-6 |
| 遗忘精度 | 创建 50 条记忆（20 高重要性 + 30 低重要性），触发 recalculate_importance，检查被标记为 cold 的集合 | 高重要性记忆误删率 < 5% |
| 冲突检测准确率 | 50 对记忆（25 对冲突 + 25 对不冲突），跑 contradiction_service | Precision ≥ 0.85, Recall ≥ 0.80 |
| 过期清理 | 创建带 TTL 的记忆，模拟过期，检查 cleanup_expired_memories | 过期记忆 100% 清理，未过期 0% 误删 |

**实现方式**：
- `backend/app/benchmarks/lifecycle/lifecycle_quality.py` 中的 `LifecycleQualityRunner`。
- 使用 `freezegun` 库模拟时间流逝（避免真实等待）。
- 冲突检测准确率使用人工标注的 50 对记忆固件（`fixtures/conflict_pairs.json`）。

---

## 4. L2 外部基准发布（P1）

目标是发布可对外比对的行业基准评分，建立技术可信度。核心解决三个问题：**真实数据集、统计置信度、竞品可比对**。

### 4.1 真实 LongMemEval-S 评测

**现状问题**：当前仅在 10 条合成数据上测试，且 LLM 非确定性导致 70%-100% 波动，无法对外发布可信评分。

**改进方案**：

| 项 | 当前 | 改进后 |
|----|------|--------|
| 数据集 | 10 条合成 | 真实 LongMemEval-S（500 题，~115K tokens/实例） |
| 样本量 | 10 | 500（全量）或分层抽样 100（置信度 95%±5%） |
| LLM 非确定性 | 单次运行 | 每题跑 3 次，取多数票（majority vote） |
| 评分报告 | 单点准确率 | 准确率 + 95% Wilson 置信区间 |
| 评估器 | LLM Judge + Heuristic | 增加 GPT-4o-mini 作为 reader LLM（与竞品对齐） |

**数据集获取**：
```bash
wget https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/resolve/main/longmemeval_s_cleaned.json
```

**运行配置**：
- 摄入：全量 haystack_sessions（每实例 ~40 sessions）。
- 召回：top_k=10，启用 P0+P1 优化。
- 生成：GPT-4o-mini（reader LLM，与 Mem0/Zep 对齐）。
- 评判：LLM Judge（GPT-4o-mini，遵循 LongMemEval 官方协议）。
- 重复：每题 3 次，多数票。

**报告内容**：
- 总体准确率 + 95% Wilson CI。
- 按 5 种能力分类（信息提取/多会话推理/时间推理/知识更新/弃权）。
- 与竞品对比表（Mem0 49.0%、Zep 71.2-94.7%、Letta —）。
- P0/P1 优化前后对比。
- 召回质量指标（Precision@10/Recall@10）作为辅助证据。

### 4.2 LoCoMo 基准评测

**现状**：竞品对标报告中明确标注"LoCoMo 基准 ❌ 未发布"，而 Zep（94.7%）和 Letta（74.0%）均已发布。

**LoCoMo 简介**：
- 1540 道问题，覆盖多轮对话长期记忆。
- 评测维度：记忆提取、时间推理、弃权、多跳推理。
- 与 LongMemEval 互补：LongMemEval 侧重会话级，LoCoMo 侧重多跳推理。

**实现方案**：

| 项 | 方案 |
|----|------|
| 数据集 | 从 LoCoMo 官方仓库获取（GitHub: snap-research/locomo） |
| 适配器 | 新增 `backend/app/benchmarks/locomo_adapter.py`，复用 `longmemeval_adapter.py` 的摄入/召回/生成流程 |
| 评估器 | 复用 `evaluator.py` 的 LLM Judge + Heuristic |
| 样本量 | 分层抽样 200 题（置信度 95%±7%），或全量 1540 题 |
| 重复 | 每题 3 次，多数票 |

**报告内容**：
- 总体准确率 + 95% Wilson CI。
- 按维度分类（提取/时间/弃权/多跳）。
- 与竞品对比表（Zep 94.7%、Letta 74.0%）。

### 4.3 LLM 非确定性稳定性评测

**目的**：量化并控制 LLM 非确定性对基准评分的影响，使发布的评分可信。

**方法**：
- 对同一组 50 题，用相同配置运行 10 次。
- 统计：
  - 准确率均值/标准差/极差。
  - 逐题稳定性（10 次中答对次数分布）。
  - 不稳定题分析（识别哪些题在 3-7 次之间波动，定位原因）。

**稳定性改进措施**（基于评测结果决定是否实施）：
- 多数票策略（3 次取多数）。
- 固定 seed（如 LLM 支持）。
- Chain-of-Note 阅读（P2 优化，减少对 LLM 推理的依赖）。

### 4.4 基准评测自动化

**CI 集成**：
- 合成数据基准（10 题，无 LLM）：每次 PR 运行，回归检测。
- 真实数据基准（500/1540 题，需 LLM）：手动触发或 nightly job。

**报告生成**：
```bash
python -m app.benchmarks run --suite longmemeval --data path/to/data --report results/
python -m app.benchmarks run --suite locomo --data path/to/data --report results/
```
- 自动生成 Markdown 报告 + JSON 原始数据。

**环境变量**：
```env
BENCHMARK_READER_LLM=gpt-4o-mini
BENCHMARK_JUDGE_LLM=gpt-4o-mini
BENCHMARK_REPEAT=3
BENCHMARK_SAMPLE_SIZE=500  # 或 0 表示全量
```

---

## 5. L3 性能与规模评测（P2）

目标是明确独立记忆系统的性能边界，供用户容量规划与竞品性能对比。

### 5.1 延迟基准（Latency Benchmark）

**指标与目标**：

| 操作 | 指标 | 目标 |
|------|------|------|
| 记忆召回（top_k=10） | P50 / P95 / P99 | P99 < 500ms |
| 语义搜索（向量） | P50 / P95 / P99 | P99 < 300ms |
| 混合搜索（4 信号融合） | P50 / P95 / P99 | P99 < 800ms |
| 片段创建（含 embedding） | P50 / P95 / P99 | P99 < 200ms |
| 图谱邻居查询（depth=2） | P50 / P95 / P99 | P99 < 300ms |
| 自动召回（auto_recall） | P50 / P95 / P99 | P99 < 1s |

**数据规模梯度**：
- 1K / 10K / 100K 片段三个量级。
- 每个量级下对每类操作跑 1000 次取分位数。

**环境**：
- 后端：单实例（4 worker）。
- 存储：SQLite + ChromaDB 本地（开发配置）/ PostgreSQL + ChromaDB 容器（生产配置）两组。
- LLM：mock 模式（排除 LLM 延迟干扰），单独测 LLM 相关路径时用真实 LLM。

### 5.2 摄入吞吐基准（Ingestion Throughput）

**目的**：量化批量摄入能力（如首次集成时导入历史会话）。

**测试场景**：
- 批量创建 10K 记忆片段（含向量嵌入）。
- 批量导入 1K 结构化表记录。
- 批量抽取 100 段会话的记忆（LLM 抽取）。

**指标**：
- 吞吐量（ops/sec）。
- 总耗时。
- 错误率。
- Outbox 积压量（跨存储一致性延迟）。

**实现**：
- `backend/app/benchmarks/performance/throughput.py`。
- 并发度梯度：1 / 4 / 8 / 16 并发。
- 记录 ChromaDB 同步延迟（Outbox 入队到完成的时间差 P95）。

### 5.3 并发负载基准（Concurrency Load）

**目的**：验证多租户并发场景下的稳定性与资源占用。

**负载模型**：
- 模拟 10 / 50 / 100 并发 workspace。
- 每个 workspace 持续混合操作：70% 召回 + 20% 写入 + 10% 图谱查询。
- 持续 10 分钟。

**监控指标**：
- 错误率（目标 < 1%）。
- P99 延迟（目标 < 2s）。
- 内存占用峰值。
- SQLite 文件大小增长 / PostgreSQL 连接池使用率。
- ChromaDB 内存占用。

**工具**：
- 使用 `locust` 或 `asyncio` + `httpx` 自定义压测脚本。
- `backend/app/benchmarks/performance/load_test.py`。

### 5.4 存储增长基准（Storage Growth）

**目的**：量化记忆存储的空间成本，供用户容量规划。

**测量方法**：
- 创建 N 条记忆（1K/10K/100K），测量：
  - SQLite/PostgreSQL 数据文件大小。
  - ChromaDB 持久化目录大小。
  - Redis 内存占用（缓存部分）。
- 计算每条记忆的平均存储成本。
- 测量 Outbox 清理后 + 过期记忆清理后的空间回收率。

**输出**：
- 存储成本表：`每 10K 记忆 ≈ X MB SQLite + Y MB ChromaDB`。
- 增长曲线图数据。

### 5.5 性能回归检测

**CI 集成**：
- 小规模性能基准（1K 片段，核心操作各 100 次）：每次 PR 运行。
- 阈值告警：P99 延迟相比基线退化 > 20% 时标记为性能回归。

**基线管理**：
- `backend/app/benchmarks/baseline.json`：记录上次发布的性能基线。
- 评测完成后自动对比，生成退化/提升报告。

---

## 6. 评测架构与实现设计

### 6.1 评测模块目录结构

```
backend/app/benchmarks/
├── __init__.py
├── runner.py                    # 现有：LongMemEval 端到端运行器（保留）
├── evaluator.py                 # 现有：LLM Judge + Heuristic（保留扩展）
├── longmemeval_adapter.py       # 现有：LongMemEval 适配器（保留扩展）
├── sample_data.py               # 现有：10 条合成数据（保留）
├── locomo_adapter.py            # 新增：LoCoMo 适配器
│
├── base.py                      # 新增：BenchmarkSuite ABC 基类
│
├── quality/                     # 新增：L1 召回质量
│   ├── __init__.py
│   ├── recall_quality.py        # RecallQualityEvaluator (P/R/F1/MRR/NDCG)
│   └── ground_truth.py          # 人工标注的 ground truth 固件
│
├── equivalence/                 # 新增：L1 SDK 等价性
│   ├── __init__.py
│   ├── sdk_equivalence.py       # SdkEquivalenceRunner
│   └── ts_runner.js             # TypeScript SDK 调用桥（node 子进程）
│
├── consistency/                 # 新增：L1 后端一致性
│   ├── __init__.py
│   └── backend_consistency.py   # BackendConsistencyRunner
│
├── isolation/                   # 新增：L1 多租户隔离
│   ├── __init__.py
│   └── multi_tenancy.py         # MultiTenancyRunner
│
├── lifecycle/                   # 新增：L1 生命周期质量
│   ├── __init__.py
│   ├── lifecycle_quality.py     # LifecycleQualityRunner
│   └── conflict_fixtures.py     # 50 对冲突检测固件
│
├── performance/                 # 新增：L3 性能基准
│   ├── __init__.py
│   ├── latency.py               # LatencyBenchmark
│   ├── throughput.py            # ThroughputBenchmark
│   ├── load_test.py             # LoadTestRunner
│   └── storage_growth.py        # StorageGrowthBenchmark
│
├── stability/                   # 新增：L2 LLM 稳定性
│   ├── __init__.py
│   └── llm_stability.py         # LlmStabilityRunner
│
├── report.py                    # 新增：统一报告生成器
├── baseline.json                # 新增：性能/质量基线
└── fixtures/                    # 新增：评测数据固件
    ├── recall_ground_truth.json     # 召回质量标注
    ├── conflict_pairs.json          # 冲突检测固件
    ├── sdk_operations.json          # SDK 等价性操作序列
    ├── backend_operations.json      # 后端一致性操作序列
    └── isolation_scenarios.json     # 多租户隔离场景
```

### 6.2 统一运行器接口

所有评测套件实现统一接口，便于 CLI 统一调度：

```python
# backend/app/benchmarks/base.py
from abc import ABC, abstractmethod
from typing import Dict, Any

class BenchmarkSuite(ABC):
    """所有评测套件的基类"""

    name: str                    # 套件名称，如 "recall_quality"
    level: str                   # "L1" | "L2" | "L3"
    requires_llm: bool = False   # 是否需要 LLM
    requires_external: bool = False  # 是否需要外部服务（PG/Milvus/TS SDK）

    @abstractmethod
    def setup(self, config: Dict[str, Any]) -> None:
        """初始化评测环境（创建 workspace、摄入数据等）"""

    @abstractmethod
    def run(self) -> Dict[str, Any]:
        """执行评测，返回原始结果"""

    @abstractmethod
    def evaluate(self, raw_results: Dict[str, Any]) -> Dict[str, Any]:
        """计算指标，返回 {metrics, passed, targets}"""

    def teardown(self) -> None:
        """清理评测数据"""
```

### 6.3 统一 CLI 入口

扩展现有 `runner.py` 的 CLI，支持所有套件：

```bash
# L1 内部质量（无 LLM，快速）
python -m app.benchmarks run --suite recall_quality
python -m app.benchmarks run --suite sdk_equivalence
python -m app.benchmarks run --suite backend_consistency
python -m app.benchmarks run --suite multi_tenancy
python -m app.benchmarks run --suite lifecycle_quality
python -m app.benchmarks run --level L1

# L2 外部基准（需 LLM + 真实数据集）
python -m app.benchmarks run --suite longmemeval --data path/to/data --repeat 3
python -m app.benchmarks run --suite locomo --data path/to/data --repeat 3
python -m app.benchmarks run --suite llm_stability --repeat 10

# L3 性能（无 LLM，但耗时）
python -m app.benchmarks run --suite latency --scale 1k
python -m app.benchmarks run --suite latency --scale 100k
python -m app.benchmarks run --suite throughput --concurrency 8
python -m app.benchmarks run --suite load_test --duration 600
python -m app.benchmarks run --suite storage_growth

# 全量
python -m app.benchmarks run --all

# 生成报告
python -m app.benchmarks report --output results/ --format markdown
```

### 6.4 报告格式

统一报告结构，每个套件输出一个章节：

```markdown
# 评测报告
> 日期: 2026-07-24 | 版本: v0.3.0 | 环境: dev

## 1. 召回质量 (L1)
| 指标 | 值 | 目标 | 通过 |
|------|-----|------|------|
| Precision@5 | 0.85 | ≥0.80 | ✓ |
| Recall@10 | 0.92 | ≥0.90 | ✓ |
...
### 召回路径对比
| 路径 | P@5 | R@10 | F1 |
|------|-----|------|-----|
| 标准召回 | 0.80 | 0.88 | 0.84 |
| P0 advanced | 0.85 | 0.92 | 0.88 |
| P1 advanced_v2 | 0.85 | 0.92 | 0.88 |

## 汇总
- L1 通过: 5/5
- L2 通过: 2/3 (locomo 待运行)
- L3 通过: 3/5 (load_test 待运行)
```

### 6.5 与现有基础设施的复用

| 现有资产 | 复用方式 |
|----------|----------|
| `longmemeval_adapter.py` | 摄入/召回/生成流程直接复用，新增 LoCoMo 适配器参考其结构 |
| `evaluator.py` | LLM Judge + Heuristic 评估器复用，扩展支持 Wilson CI 计算 |
| `sample_data.py` | 作为 L1 召回质量的部分数据源 |
| `conftest.py` 的清理 fixture | 评测套件 setup/teardown 复用测试数据清理逻辑 |
| `freezegun`（测试依赖） | 生命周期评测模拟时间流逝 |
| `_cleanup_test_data_autouse` | 评测套件 teardown 参考其多表清理逻辑 |
| Prometheus metrics | 性能基准复用 `memory_recall_latency_seconds` 等 Histogram |

### 6.6 评测数据固件管理

固件文件（JSON）独立于代码，便于维护：

- `fixtures/recall_ground_truth.json`：每条含 `{question, expected_fragment_ids, capability}`。
- `fixtures/conflict_pairs.json`：每对含 `{fragment_a, fragment_b, is_conflict, reason}`。
- `fixtures/sdk_operations.json`：操作序列 `[{op, args, expect}, ...]`。
- `fixtures/backend_operations.json`：后端一致性操作序列。
- `fixtures/isolation_scenarios.json`：多租户场景定义。

固件数据需人工标注/审核，纳入版本控制。

### 6.7 CI 集成策略

| 套件 | CI 触发 | 耗时 | 失败处理 |
|------|---------|------|----------|
| recall_quality（合成数据） | 每次 PR | ~30s | 阻塞合并 |
| sdk_equivalence（Embedded vs HTTP） | 每次 PR | ~1min | 阻塞合并 |
| multi_tenancy | 每次 PR | ~30s | 阻塞合并 |
| lifecycle_quality | 每次 PR | ~1min | 阻塞合并 |
| backend_consistency | nightly | ~5min | 告警不阻塞 |
| latency（1K 规模） | nightly | ~3min | 退化 >20% 告警 |
| longmemeval（真实数据） | 手动/release | ~2h | 阻塞发布 |
| locomo（真实数据） | 手动/release | ~4h | 阻塞发布 |
| load_test | 手动/release | ~15min | 阻塞发布 |

---

## 7. 实施路线图与成功标准

### 7.1 实施阶段划分

| 阶段 | 内容 | 前置依赖 | 交付物 |
|------|------|----------|--------|
| **Phase 1：L1 核心套件** | 召回质量 + SDK 等价性 + 多租户隔离 | 无 | 3 个套件 + 固件 + CI 集成 |
| **Phase 2：L1 补全** | 后端一致性 + 生命周期质量 | Phase 1（复用 BenchmarkSuite 基类） | 2 个套件 + 冲突固件 |
| **Phase 3：L2 基准发布** | 真实 LongMemEval-S + LLM 稳定性 | 下载真实数据集 + LLM API 配额 | 对外基准报告 |
| **Phase 4：L2 补全** | LoCoMo 基准 | LoCoMo 数据集 + Phase 3（复用 CI 流程） | 对外基准报告 |
| **Phase 5：L3 性能** | 延迟 + 吞吐 + 负载 + 存储增长 | Phase 1-2（复用基类） | 性能基线 + 容量规划指南 |

每个 Phase 可独立交付价值。Phase 1-2 夯实质量底座，Phase 3-4 建立可信度，Phase 5 明确性能边界。

### 7.2 各阶段成功标准

**Phase 1 成功标准**：
- 召回质量：Precision@5 ≥ 0.80，Recall@10 ≥ 0.90。
- SDK 等价性：HTTP 与 Embedded 模式所有操作字段级等价。
- 多租户隔离：6 个隔离场景全部通过，零数据泄漏。
- CI 集成：每次 PR 自动运行，< 3min。

**Phase 2 成功标准**：
- 后端一致性：SQLite↔PG、ChromaDB↔Milvus 行为一致（搜索结果集相等）。
- 生命周期质量：半衰期误差 < 1e-6，遗忘误删率 < 5%，冲突检测 P ≥ 0.85 / R ≥ 0.80。

**Phase 3 成功标准**：
- 真实 LongMemEval-S：准确率 ≥ 70%，带 95% Wilson CI。
- LLM 稳定性：10 次运行标准差 < 10%。
- 发布可对外比对的基准报告（含竞品对比表）。

**Phase 4 成功标准**：
- LoCoMo：准确率 ≥ 60%，带 95% Wilson CI。
- 发布对外基准报告。

**Phase 5 成功标准**：
- 延迟：召回 P99 < 500ms，混合搜索 P99 < 800ms（1K 规模）。
- 负载：100 并发 workspace，错误率 < 1%，P99 < 2s。
- 发布性能基线 + 容量规划指南文档。

### 7.3 风险与缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| 真实 LongMemEval 数据集过大（500×115K tokens） | 摄入耗时极长，评测成本高 | 分层抽样 100 题（95%±5% 置信度），或按能力等比例抽样 |
| LLM API 费用（500 题 × 3 次 × 2 个 LLM） | 基准运行成本高 | Phase 3 先跑 100 题验证流程，再扩到 500 题 |
| Milvus 后端一致性测试环境搭建复杂 | Phase 2 延期 | Milvus 用 docker-compose 启动，ChromaDB↔Milvus 可降级为"语义等价"（top_k 集合相等即可） |
| TypeScript SDK 等价性需 node 子进程 | CI 环境依赖复杂 | TS 等价性降级为 nightly 运行，PR 仅跑 Python HTTP vs Embedded |
| 人工标注 ground truth 耗时 | Phase 1 延期 | 先标注 10 条合成数据，后续逐步扩充到 30 条 |

### 7.4 评测驱动功能完善的闭环

评测不仅用于验证，还用于发现功能短板并驱动开发：

```
评测发现短板 → 记录为 issue → 评估优先级 → 实施优化 → 重跑评测验证
```

**预期由评测驱动的功能完善**（基于现有评测基线推测）：

| 评测可能发现的短板 | 对应未实现的优化 | 优先级 |
|-------------------|------------------|--------|
| 召回 Recall@10 不足 | P2-1 Chain-of-Note 阅读 | P2 |
| 弃权准确率低 | P2-2 主动弃权检测机制 | P2 |
| 冲突检测 Recall 不足 | 矛盾检测模式扩充 | P1 |
| SDK Embedded 模式不等价 | EmbeddedTransport 路径补全 | P0 |
| 多租户隔离泄漏 | workspace_id 过滤遗漏修复 | P0 |

每轮评测完成后，更新 `docs/评测驱动改进清单.md`，追踪短板修复状态。

### 7.5 文档与发布

**新增文档**：
- `docs/评测方案设计.md`（本文档）
- `docs/评测驱动改进清单.md`（评测发现的短板与修复追踪）
- `docs/LongMemEval基准测试报告.md`（现有，Phase 3 更新为真实数据集版本）
- `docs/LoCoMo基准测试报告.md`（Phase 4 新增）
- `docs/性能基准报告.md`（Phase 5 新增）
- `docs/容量规划指南.md`（Phase 5 新增）

**README 更新**：
- 添加"评测"章节，列出所有基准评分与竞品对比。
- 添加"质量保证"徽章（L1 套件通过状态）。

---

*本设计文档基于 Agent Memory System v0.3.0 现状分析，参考竞品对标报告与独立记忆系统演进方案制定。*
