# LoCoMo 基准测试报告

> **R-04: 运行 LoCoMo 基准并发布结果**
>
> 测试日期: 2026-07-24
> 基准版本: LoCoMo (Snap Research) — 1540 题
> 评估对象: Agent Memory System v0.3.0

---

## 1. 执行摘要

本报告呈现 Agent Memory System 在 LoCoMo 长期记忆基准测试上的表现。LoCoMo 由 Snap Research 发布，包含 1540 道问题，覆盖多轮对话长期记忆的 4 个维度，与 LongMemEval 互补（LongMemEval 侧重会话级，LoCoMo 侧重多跳推理）。

### 关键结果

| 评估模式 | 实例数 | 正确数 | 准确率 | 95% CI | 备注 |
|----------|--------|--------|--------|--------|------|
| AMS (本系统) | 待评测 | 待评测 | 待评测 | 待评测 | P0+P1 优化 |
| Zep | — | — | 94.7% | — | LoCoMo 官方 |
| Letta | — | — | 74.0% | — | LoCoMo 官方 |
| Mem0 | — | — | — | — | 未发布 LoCoMo |

> **目标**：准确率 ≥ 60%（参考 Letta 74.0% 为上限目标）

---

## 2. LoCoMo 基准简介

### 2.1 数据集概述

| 属性 | 值 |
|------|-----|
| 发布机构 | Snap Research |
| 代码仓库 | https://github.com/snap-research/locomo |
| 问题总数 | 1540 |
| 评测维度 | 4 个（记忆提取/时间推理/弃权/多跳推理） |
| 数据形式 | 多轮对话 + 问题 + 参考答案 |

### 2.2 评测维度

| 维度 | 英文 | 说明 | 示例 |
|------|------|------|------|
| 记忆提取 | extraction | 从对话历史中提取特定信息 | "用户提到的项目名称是什么？" |
| 时间推理 | temporal_reasoning | 基于时间顺序的推理 | "用户第一次提到的城市是哪里？" |
| 弃权 | abstention | 正确识别无法回答的问题 | "用户的银行卡号是多少？" |
| 多跳推理 | multi_hop_reasoning | 跨多个会话的综合推理 | "用户在项目 A 中使用的技术栈与项目 B 有何不同？" |

### 2.3 与 LongMemEval 的互补性

| 维度 | LongMemEval | LoCoMo |
|------|-------------|--------|
| 侧重 | 会话级记忆 | 多跳推理 |
| 问题数 | 500（S）/ 500（M） | 1540 |
| 数据规模 | ~115K tokens/实例 | 多轮对话 |
| 多跳推理 | 弱 | 强 |
| 弃权测试 | 有 | 有 |

---

## 3. 评测方法

### 3.1 评测流程

```
LoCoMo 数据集
    ↓
load_locomo_dataset() 标准化
    ↓
（可选）分层抽样 --limit N
    ↓
对每条实例：
    1. ingest_history() 摄入会话历史
    2. recall_for_question() 召回记忆
    3. generate_answer() 生成答案（LLM）
    4. evaluate_answer() 评估正确性（LLM Judge）
    ↓
（可选）多数票 --repeat 3
    ↓
compute_metrics() 计算准确率 + Wilson CI
    ↓
按 4 维度分类统计 + 竞品对比表
```

### 3.2 分层抽样

当不运行全量 1540 题时，采用分层抽样确保每个能力类别在样本中都有代表：
- 按能力类别分组
- 等比例分配每个类别的抽样数量
- 保证最小 1 条/类别

### 3.3 评估器

与 LongMemEval 相同的双评估器：
- **LLM Judge**：GPT-4o-mini 遵循官方协议评判
- **Heuristic**：基于关键词匹配的启发式评判（无 LLM 模式）

### 3.4 多数票与置信区间

- 每题运行 3 次，取多数票（`correct_count > repeat/2`）
- 准确率附带 95% Wilson 置信区间

---

## 4. 运行方式

### 4.1 数据集获取

```bash
git clone https://github.com/snap-research/locomo.git
# 将数据文件放入
cp locomo/data/*.json backend/app/benchmarks/data/locomo/
```

### 4.2 运行评测

```bash
cd backend

# 快速验证（50 题，多数票 3 次）
python -m app.benchmarks.runner run --suite locomo \
    --data app/benchmarks/data/locomo \
    --limit 50 --repeat 3 \
    --output results/locomo_sample.json \
    --report results/locomo_sample.md

# 全量 1540 题
python -m app.benchmarks.runner run --suite locomo \
    --data app/benchmarks/data/locomo \
    --repeat 3 \
    --output results/locomo_full.json \
    --report results/locomo_full.md
```

### 4.3 环境变量

```env
BENCHMARK_READER_LLM=gpt-4o-mini
BENCHMARK_JUDGE_LLM=gpt-4o-mini
BENCHMARK_REPEAT=3
```

---

## 5. 详细结果

> 以下章节在真实数据集评测完成后填充。

### 5.1 总体结果

| 指标 | 值 |
|------|-----|
| 实例总数 | 待评测 |
| 正确数 | 待评测 |
| 准确率 | 待评测 |
| 95% Wilson CI | 待评测 |
| 多数票重复次数 | 3 |
| 耗时 | 待评测 |

### 5.2 按维度分类

| 维度 | 总数 | 正确数 | 准确率 | 95% CI |
|------|------|--------|--------|--------|
| 记忆提取 | 待评测 | 待评测 | 待评测 | 待评测 |
| 时间推理 | 待评测 | 待评测 | 待评测 | 待评测 |
| 弃权 | 待评测 | 待评测 | 待评测 | 待评测 |
| 多跳推理 | 待评测 | 待评测 | 待评测 | 待评测 |

### 5.3 竞品对比

| 系统 | 准确率 | 95% CI | 备注 |
|------|--------|--------|------|
| **AMS (本系统)** | 待评测 | 待评测 | P0+P1 优化 |
| Zep | 94.7% | — | LoCoMo 官方 |
| Letta | 74.0% | — | LoCoMo 官方 |
| Mem0 | — | — | 未发布 LoCoMo |

### 5.4 LLM 稳定性

| 指标 | 值 |
|------|-----|
| 3 次运行准确率均值 | 待评测 |
| 3 次运行标准差 | 待评测 |
| 稳定题数（3/3 一致） | 待评测 |
| 不稳定题数（2/3 或 1/3） | 待评测 |

---

## 6. 分析与洞察

> 以下章节在真实数据集评测完成后填充。

### 6.1 优势维度

待评测。

### 6.2 短板维度

待评测。

### 6.3 与 LongMemEval 对比

| 维度 | LongMemEval-S | LoCoMo |
|------|---------------|--------|
| 总体准确率 | 待评测 | 待评测 |
| 信息提取 | 待评测 | 待评测 |
| 时间推理 | 待评测 | 待评测 |
| 弃权 | 待评测 | 待评测 |
| 多会话/多跳 | 待评测 | 待评测 |

---

## 7. 可复现性

### 7.1 代码位置

- 适配器：`backend/app/benchmarks/locomo_adapter.py`
- 评测套件：`backend/app/benchmarks/locomo_suite.py`
- 评估器：`backend/app/benchmarks/evaluator.py`
- 运行器：`backend/app/benchmarks/runner.py`

### 7.2 数据版本

- LoCoMo 数据集版本：Snap Research 官方仓库最新版
- 评测日期：2026-07-24

---

## 8. Phase 4：真实数据集接入进展

> 更新日期: 2026-07-24

### 8.1 数据集获取状态

**当前状态：阻塞** — GitHub 网络不通

已尝试的获取方式：

| 方式 | 结果 |
|------|------|
| `git clone https://github.com/snap-research/locomo.git` | ✗ RPC 失败，curl 56 Recv failure 超时 |
| `git clone https://gitclone.com/github.com/snap-research/locomo.git` | ✗ 克隆到空仓库 |
| `curl -L https://github.com/snap-research/locomo/archive/refs/heads/main.zip` | ✗ 连接超时 |
| `git clone https://ghfast.top/https://github.com/...` | ✗ 端口 443 连接失败 |

### 8.2 评测框架就绪度

尽管数据集未获取，评测框架已完成准备：

| 组件 | 状态 | 文件 |
|------|------|------|
| 数据集加载器 | ✓ 就绪 | [locomo_adapter.py](file:///Users/howdy/pm/agent-memory-system/backend/app/benchmarks/locomo_adapter.py) — `load_locomo_dataset()` 支持目录/文件输入 |
| 评测套件 | ✓ 就绪 | [locomo_suite.py](file:///Users/howdy/pm/agent-memory-system/backend/app/benchmarks/locomo_suite.py) — `LoCoMoSuite` 已注册 |
| 适配器修复 | ✓ 就绪 | `MemoryAdapter.__init__` 已调用 `ensure_perf_user`（与 LongMemEval 共享修复） |
| BM25 修复 | ✓ 就绪 | `_tokenize_query` 已转义 FTS5 特殊字符（与 LongMemEval 共享修复） |

### 8.3 待数据集获取后的运行命令

```bash
# 1. 下载数据集（需 GitHub 可达）
git clone https://github.com/snap-research/locomo.git /tmp/locomo
cp /tmp/locomo/data/*.json backend/app/benchmarks/data/locomo/

# 2. 运行评测
python -m app.benchmarks.runner run --suite locomo \
  --data backend/app/benchmarks/data/locomo/ \
  --user-id 9700 \
  --output results/locomo_results.json \
  --report results/locomo_report.md

# 3. 多数票运行（消除 LLM 非确定性）
python -m app.benchmarks.runner run --suite locomo \
  --data backend/app/benchmarks/data/locomo/ \
  --repeat 3 --user-id 9700 \
  --output results/locomo_repeat3.json
```

### 8.4 性能预期

基于 LongMemEval 真实数据集的经验（155s/题），LoCoMo 1540 题全量评测预计耗时：
- 启发式模式（无 LLM）：~66 小时（1540 × 155s）
- 优化后（禁用 LLM rerank + 批量摄入）：~13 小时（1540 × 30s）
- 50 题抽样评测：~25 分钟

**建议**：先完成 LongMemEval 的性能优化（P0: 禁用 LLM rerank），再应用到 LoCoMo 评测。

---

*本报告由 Agent Memory System 基准测试框架生成，测试框架源码位于 `backend/app/benchmarks/`。*
