# LoCoMo 基准测试报告

> **R-04: 运行 LoCoMo 基准并发布结果**
>
> 测试日期: 2026-07-28
> 基准版本: LoCoMo (Snap Research) — locomo10.json，1986 道 QA（含对抗弃权题；官方 1540 题口径 = 剔除 category 5）
> 评估对象: Agent Memory System v0.3.0

---

## 1. 执行摘要

本报告呈现 Agent Memory System 在 LoCoMo 长期记忆基准测试上的表现。LoCoMo 由 Snap Research 发布，包含 1540 道问题，覆盖多轮对话长期记忆的 4 个维度，与 LongMemEval 互补（LongMemEval 侧重会话级，LoCoMo 侧重多跳推理）。

### 关键结果

| 评估模式 | 实例数 | 正确数 | 准确率 | 95% CI | 备注 |
|----------|--------|--------|--------|--------|------|
| **AMS (本系统)** | **141**（分层抽样 200 题，中途终止） | **40** | **28.4%** | **[21.6%, 36.3%]** | default embedding，DeepSeek V4 Flash reader/judge |
| Zep | — | — | 94.7% | — | LoCoMo 官方 |
| Letta | — | — | 74.0% | — | LoCoMo 官方 |
| Mem0 | — | — | — | — | 未发布 LoCoMo |

> **重要说明**：本轮为分层抽样 200 题（seed=42）的部分结果——评测于第 141 题处按用户指令手动终止，未启用多数票（repeat=1）。结果 JSON：`backend/results/locomo141_default_partial.json`。
> **目标**：准确率 ≥ 60%（参考 Letta 74.0% 为上限目标）——当前未达标，短板分析见 §6。

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

当不运行全量题目时，采用分层抽样确保每个能力类别在样本中都有代表：
- 按能力类别分组，等比例分配每个类别的抽样数量（保证最小 1 条/类别）
- 固定 `seed=42` 随机抽样，结果可复现
- 抽样后按 `haystack_group`（对话组）聚类排序，同一对话的题目复用已摄入记忆（摄入开销降低 ~20 倍）

本轮 200 题抽样分布（seed=42）：信息提取 85 / 弃权 45 / 时间推理 32 / 多跳推理 28 / 开放域 10。

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

> 评测配置：分层抽样 200 题（seed=42），EMBEDDING_PROVIDER=default（Chroma 内置 all-MiniLM-L6-v2），Reader/Judge 均为 DeepSeek V4 Flash，top_k=10，repeat=1。隔离环境（fakeredis + 临时 SQLite/Chroma 目录）。评测于第 141 题处按用户指令终止，以下为 141 题部分结果。

### 5.1 总体结果

| 指标 | 值 |
|------|-----|
| 实例总数 | 141（抽样 200 题中已完成部分） |
| 正确数 | 40 |
| 准确率 | **28.4%** |
| 95% Wilson CI | [21.6%, 36.3%] |
| 多数票重复次数 | 1（未启用多数票） |
| 耗时 | 约 9 小时（与 LongMemEval 评测并行，含 8 个对话组摄入） |
| LLM 调用 | 约 666 次（DeepSeek V4 Flash） |

### 5.2 按维度分类

| 维度 | 总数 | 正确数 | 准确率 | 95% CI |
|------|------|--------|--------|--------|
| 记忆提取 (information_extraction) | 59 | 21 | 35.6% | [24.6%, 48.3%] |
| 时间推理 (temporal_reasoning) | 22 | 9 | 40.9% | [23.3%, 61.3%] |
| 弃权 (abstention) | 32 | 5 | 15.6% | [6.9%, 31.8%] |
| 多跳推理 (multi_hop_reasoning) | 19 | 5 | 26.3% | [11.8%, 48.8%] |
| 开放域 (open_domain_knowledge) | 9 | 0 | 0.0% | [0.0%, 29.9%] |

### 5.3 竞品对比

| 系统 | 准确率 | 95% CI | 备注 |
|------|--------|--------|------|
| **AMS (本系统)** | **28.4%** | [21.6%, 36.3%] | 141 题部分结果，default embedding，repeat=1 |
| Zep | 94.7% | — | LoCoMo 官方 |
| Letta | 74.0% | — | LoCoMo 官方 |
| Mem0 | — | — | 未发布 LoCoMo |

> 注：竞品分数采用各自官方配置（通常为 GPT-4o 级 reader + 全量题目），与本轮配置（DeepSeek V4 Flash + 抽样部分结果）不可直接对比，仅作参考。

### 5.4 LLM 稳定性

本轮 repeat=1，未启用多数票与稳定性统计；后续完整跑分建议 `--repeat 3`。

---

## 6. 分析与洞察

> 基于 141 题部分结果（default embedding，repeat=1），结论需谨慎解读。

### 6.1 相对优势维度

- **时间推理（40.9%）与记忆提取（35.6%）**：高于总体均值，说明会话分解索引 + 时间感知召回策略在 LoCoMo 的多轮对话场景中部分有效
- 同组记忆复用机制验证成功：同一对话组的后续题目无需重新摄入，平均单题耗时从分钟级降至约 7 秒

### 6.2 短板维度

- **开放域（0/9）**：LoCoMo 开放域题需要结合对话外的常识推理，当前 prompt 严格限制"仅基于记忆上下文作答"，导致此类题全部失分
- **弃权（15.6%）**：LoCoMo 对抗弃权题（adversarial）与 LongMemEval 弃权题（95%）表现差异巨大——LoCoMo 的对抗题在对话中存在高度相似的干扰信息，reader 倾向于基于相似记忆强行作答而非弃权
- **多跳推理（26.3%）**：跨会话证据聚合能力不足，召回的 top_k=10 片段往往只覆盖单跳证据
- 与竞品的差距还源于配置差异：竞品官方分数普遍使用 GPT-4o 级 reader + 专为 LoCoMo 调优的记忆管道；本轮为通用管道 + DeepSeek V4 Flash，未做数据集特化调优

### 6.3 与 LongMemEval 对比

| 维度 | LongMemEval-S（100 题） | LoCoMo（141 题部分） |
|------|---------------|--------|
| 总体准确率 | 70.0% [60.4%, 78.1%] | 28.4% [21.6%, 36.3%] |
| 信息提取 | 65.0% | 35.6% |
| 时间推理 | 60.0% | 40.9% |
| 弃权 | 95.0% | 15.6%（对抗弃权） |
| 多会话/多跳 | 45.0%（多会话推理） | 26.3%（多跳推理） |

结论：AMS 的记忆管道对 LongMemEval 的"用户事实记忆"场景适配较好，但对 LoCoMo 的"双人长对话 + 对抗干扰"场景仍有明显差距，后续优先改进对抗弃权判别与跨会话证据聚合。

---

## 7. 可复现性

### 7.1 代码位置

- 适配器：`backend/app/benchmarks/locomo_adapter.py`
- 评测套件：`backend/app/benchmarks/locomo_suite.py`
- 评估器：`backend/app/benchmarks/evaluator.py`
- 运行器：`backend/app/benchmarks/runner.py`

### 7.2 数据版本

- LoCoMo 数据集版本：Snap Research 官方仓库 `locomo10.json`（10 个对话样本，1986 道 QA）
- 评测日期：2026-07-28
- 复现命令：

```bash
cd backend
PYTHONPATH=. REDIS_URL=fakeredis:// python -m app.benchmarks.runner run \
    --suite locomo --data app/benchmarks/data/locomo \
    --limit 200 --user-id 9911 \
    --output results/locomo200_default.json
# 分层抽样固定 seed=42，抽样结果可复现
```

---

## 8. 真实数据集接入进展

> 更新日期: 2026-07-28 — **已完成**（此前 GitHub 直连阻塞问题已通过网络恢复解决）

| 事项 | 状态 |
|------|------|
| 数据集获取 | ✓ `git clone snap-research/locomo` → `backend/app/benchmarks/data/locomo/locomo10.json`（2.7MB，10 对话，1986 QA） |
| 格式标准化 | ✓ `locomo_adapter.py` 重写：数字 category 映射（1=多跳/2=时间/3=开放域/4=提取/5=对抗弃权）、对话展开为标准实例、speaker 前缀保留 |
| 分层抽样 | ✓ `locomo_suite.py` 支持 seed=42 等比例抽样 + haystack_group 聚类排序 |
| 记忆复用 | ✓ `runner.py` 同对话组仅摄入一次（摄入开销降约 20 倍） |
| 首轮跑分 | ✓ 141/200 题部分结果（default embedding），见 §5 |

### 待办

- 补齐剩余 59 题 + local (BGE) embedding A/B 对比轮（本轮按用户指令跳过）
- 启用 `--repeat 3` 多数票消除 LLM 非确定性
- 针对 §6.2 短板（对抗弃权/多跳聚合）迭代后复测

---

*本报告由 Agent Memory System 基准测试框架生成，测试框架源码位于 `backend/app/benchmarks/`。*
