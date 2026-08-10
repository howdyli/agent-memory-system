# Agent Memory System 前端 UI 操作测试手册

> 版本：v1.0（2026-07-30）
> 依据：三批浏览器实测记录（批次1 认证与基础记忆、批次2 检索与高级记忆、批次3 Agent 与系统集成）+ 21 张实测截图
> 路由权威依据：`frontend/src/App.tsx`
> 面向对象：人工测试操作者

---

## 目录

1. [快速开始](#1-快速开始)
2. [页面导航地图](#2-页面导航地图)
3. [分页面测试用例](#3-分页面测试用例)
4. [端到端流程验证](#4-端到端流程验证)
5. [已知问题与环境差异](#5-已知问题与环境差异)

---

## 1. 快速开始

### 1.1 环境要求

| 项目 | 要求 | 说明 |
|------|------|------|
| Python | 3.13+ | 必须使用 `backend/.venv` 虚拟环境（依赖齐全）；**项目根目录 `.venv` 是空壳**（无 pip、无任何包），用它启动会报 `No module named uvicorn` |
| Node.js / npm | Node 16+ | 前端 `frontend/node_modules` 已就绪时可跳过 `npm install` |
| 数据库 | SQLite（内置） | **真实数据库是 `backend/agent_memory.db`（约 57MB，含 admin 用户）**；根目录同名 `agent_memory.db` 仅 4KB 且 users 表为空，无法登录 |
| Redis | 可选 | 未启动时后端自动回退 fakeredis，不影响功能测试 |
| LLM 后端 | 可选 | 未配置时 AgentChat / Extraction 的 LLM 生成能力降级（见 §5.2） |

### 1.2 启动命令（含数据隔离，经实战验证）

**重要：SQLite 场景下 `DATABASE_URL` 环境变量不生效。** 代码中 `SQLiteClient` 硬编码相对路径 `agent_memory.db`，按进程 CWD 解析。因此数据隔离的正确做法是"复制数据 + 切换 CWD"，而不是设置环境变量。

**第一步：准备隔离数据目录**（避免污染真实数据）：

```bash
mkdir -p /tmp/ams-ui-test
# 复制真实数据库（必须连同 -shm / -wal 一起复制）
cp pm/agent-memory-system/backend/agent_memory.db* /tmp/ams-ui-test/
# 复制向量库数据
cp -r pm/agent-memory-system/backend/chromadb_data /tmp/ams-ui-test/
```

**第二步：以隔离目录为 CWD 启动后端**（端口 8000）：

```bash
cd /tmp/ams-ui-test
<项目路径>/backend/.venv/bin/python -m uvicorn app.main:app \
  --app-dir <项目路径>/backend \
  --host 127.0.0.1 --port 8000
```

启动后可用 `lsof -p <PID>` 确认后端只打开了 `/tmp/ams-ui-test/` 下的数据文件。健康检查：`http://localhost:8000/api/v1/health`；Swagger：`http://localhost:8000/docs`。

启动日志中的两条**非致命警告**属正常现象：
- `JWT_SECRET_KEY 未设置，已生成临时密钥`——后端重启后旧 token 失效，测试期间不要重启后端；
- `真实 Redis 连接失败 (localhost:6379)`——自动回退 fakeredis，不影响功能。

**第三步：启动前端 dev 服务**（端口 5173，vite 已配置 `/api → localhost:8000` 代理）：

```bash
cd pm/agent-memory-system/frontend
npm run dev
# 浏览器访问 http://localhost:5173
```

**测试结束清理：**

```bash
kill $(lsof -ti :8000 -sTCP:LISTEN)   # 停后端
kill $(lsof -ti :5173 -sTCP:LISTEN)   # 停前端
rm -rf /tmp/ams-ui-test               # 删除隔离数据
```

### 1.3 默认账号

| 项目 | 值 | 说明 |
|------|-----|------|
| 用户名 | `admin` | user_id=3 |
| 密码 | `admin123` | **注意：README 所载 `Admin123!` 有误**。经数据库密码哈希验证，实际有效密码为 `admin123`（登录 API 实测返回 token 成功） |

### 1.4 默认端口

| 服务 | 端口 |
|------|------|
| 后端 API | 8000 |
| 前端（本地 dev 模式，vite） | 5173 |
| 前端（Docker Compose，`--profile full`） | 3000 |

---

## 2. 页面导航地图

以下 17 个路由均在 `frontend/src/App.tsx` 中挂载，除 `/login` 外全部受 AuthGuard 保护（未登录自动跳转登录页）；未匹配路径重定向到 `/`。

| # | 路由 | 页面 | 功能 | 依赖数据 |
|---|------|------|------|---------|
| 1 | `/login` | Login | 用户名/密码登录，获取 JWT | users 表需含有效账号 |
| 2 | `/` | Dashboard | 统计卡片、快速操作、最近对话、系统健康状态 | 各类记忆统计、会话记录 |
| 3 | `/agent-chat` | AgentChat | Agent 多轮对话 + 记忆自动召回 | 会话历史；LLM 后端（生成回复需配置） |
| 4 | `/agent-tools` | AgentTools | Agent 工具测试台（13 个工具，OpenAI Function Calling 格式） | 记忆数据（工具执行结果依赖） |
| 5 | `/variables` | Variables | 记忆变量（KV）CRUD | memory_variables |
| 6 | `/extraction` | Extraction | 记忆抽取（4 个 Tab） | LLM 后端（实际抽取需配置） |
| 7 | `/tables` | Tables | 记忆表（动态 schema）与记录管理 | memory_tables |
| 8 | `/fragments` | Fragments | 记忆片段列表/新建/编辑/删除 + 语义搜索 | memory_fragments、向量库 |
| 9 | `/recall` | Recall | 自动召回（上下文感知） | 片段数据、embedding 服务 |
| 10 | `/long-term` | LongTerm | 长期记忆（版本控制 + 审计） | long_term 数据；**创建 API 当前 404，见 §5.1** |
| 11 | `/observability` | Observability | 观测中心（7 个选项卡：仪表盘/追踪/指标等） | 观测指标与追踪事件 |
| 12 | `/lifecycle` | Lifecycle | 生命周期管理（6 个 Tab：衰减/归档/清理等） | 生命周期配置与任务记录 |
| 13 | `/graph-memory` | GraphMemory | 知识图谱（实体/关系可视化，3 种布局） | 图谱实体与关系数据 |
| 14 | `/hybrid-search` | HybridSearch | 混合搜索（向量 + BM25 + 时间融合） | 片段数据、向量库 |
| 15 | `/playground` | Playground | 记忆调试工具（注入/召回/衰减/演化 4 个模拟器） | playground API |
| 16 | `/system` | System | 系统集成（LLM 状态/后端配置/插件/性能/安全 5 个选项卡） | LLM 后端配置 |
| 17 | `/workspace/settings` | WorkspaceSettings | Workspace 管理 + API Key 管理 | workspace、api_keys |

> **注记**：`pages/` 目录下另有 `About.tsx`、`Home.tsx` 两个页面组件**未挂载任何路由**（App.tsx 中无对应 Route），无法通过 URL 访问，不在本手册测试范围内。

---

## 3. 分页面测试用例

每节统一模板：**前置条件 / 操作步骤 / 预期结果 / 验证要点 / 实测截图 / 已知问题**。所有用例均来自三批实测的真实行为记录。

> 遗留实测数据可直接作为示例数据引用：变量 `uitest1_user_name`、`uitest1_user_age`、`uitest1_user_email`；`uitest1_` 前缀片段 ×2；记忆表 `uitest1_contacts`（2 条记录）；对话会话 `uitest3_新建对话测试`。

### 3.1 Login（/login）

**前置条件**：后端 8000、前端 5173 已启动；数据库含 admin 账号。

**用例 L-1：登录页可达与初始渲染**
1. 浏览器访问 `http://localhost:5173/login`。
- **预期结果**：登录页正常渲染，显示用户名、密码输入框与登录按钮。
- **验证要点**：无控制台报错；表单控件可聚焦。
- **实测截图**：`./ui-manual-screenshots/batch1_login_initial.png`、`./ui-manual-screenshots/batch1_login.png`

**用例 L-2：错误凭据校验提示**
1. 输入错误的用户名/密码组合（如 `admin` / 错误密码）。
2. 点击登录。
- **预期结果**：登录失败，页面给出校验/错误提示，不跳转。
- **验证要点**：错误提示可见；token 未写入本地存储。
- **实测截图**：`./ui-manual-screenshots/batch1_login_filled.png`（填写状态）、`./ui-manual-screenshots/batch1_login_validation_error.png`（校验错误提示）

**用例 L-3：正确凭据登录成功**
1. 输入 `admin` / `admin123`。
2. 点击登录。
- **预期结果**：登录成功，跳转到 Dashboard（`/`）。
- **验证要点**：登录 API 返回 token（user_id=3）；后续页面访问不再跳回登录页。
- **实测截图**：`./ui-manual-screenshots/batch1_login_correct_filled.png`

**用例 L-4：未登录访问受保护路由**
1. 清除登录态后直接访问任一受保护路由（如 `/variables`）。
- **预期结果**：AuthGuard 拦截，自动跳转 `/login`。
- **验证要点**：不出现受保护页面内容闪现后报错的情况。

**已知问题**：无产品缺陷。附注：自动化脚本直接改 DOM 的 `input.value` 无法触发 React 受控组件状态更新，需用 `nativeInputValueSetter` + 合成事件注入；**人工键盘输入完全正常**，此为自动化工具限制而非产品问题（见 §5.3）。

### 3.2 Dashboard（/）

**前置条件**：已登录。

**用例 D-1：统计卡片数据展示**
1. 登录后进入 `/`。
- **预期结果**：统计卡片正常显示各类记忆数量（实测环境：片段 175+、变量 3、表 2 等 180+ 条数据）。
- **验证要点**：数字与 Variables/Fragments/Tables 各页实际列表数量一致。
- **实测截图**：`./ui-manual-screenshots/batch1_dashboard.png`

**用例 D-2：快速操作入口**
1. 点击 Dashboard 上的快速操作按钮（如进入记忆片段管理）。
- **预期结果**：正确跳转对应功能页面。
- **验证要点**：批次3 实测通过快速按钮成功进入片段管理页。

**用例 D-3：最近对话与系统健康状态**
1. 查看"最近对话"列表与系统健康状态区。
- **预期结果**：最近对话展示历史会话（实测含 `uitest3_新建对话测试` 等，今日 5 条历史记录）；健康状态显示后端、数据库、Redis、向量库全部正常。
- **验证要点**：在 AgentChat 新建会话后回到 Dashboard，最近对话列表同步出现新会话。

**已知问题**：无。

### 3.3 AgentChat（/agent-chat）

**前置条件**：已登录；完整回复需配置 LLM 后端（未配置时降级，见 §5.2）。

**用例 AC-1：页面加载与会话列表**
1. 访问 `/agent-chat`。
- **预期结果**：页面标题"Agent 对话"，加载 <3s；左侧会话列表展示历史对话（实测 15+ 条）；可见快速示例按钮："记住我的信息"、"测试记忆召回"、"查询所有记忆"。
- **验证要点**：会话列表可点击切换；消息输入框支持多行输入。
- **实测截图**：`./ui-manual-screenshots/batch3_agent_chat.png`

**用例 AC-2：发送按钮状态校验**
1. 清空输入框，观察发送按钮。
2. 输入任意文本，再观察发送按钮。
- **预期结果**：空输入时发送按钮 disabled；有输入时 enabled。
- **验证要点**：状态切换即时无延迟。

**用例 AC-3：新建对话并发送消息（含记忆召回）**
1. 点击"新建对话"。
2. 输入消息（实测示例：`uitest3_新建对话测试`），点击发送。
- **预期结果**：消息即时显示在对话框中；系统自动触发"召回记忆中..."；召回完成后显示"本次回复参考了 4 条记忆"（实测值）；新会话自动出现在左侧列表与 Dashboard 最近对话中，时间戳正确（实测 07/30 08:19）。
- **验证要点**：消息发送即时；记忆召回过程约 5–10s（含 embedding，属正常）；会话标题自动生成（取用户消息内容）。
- **已知环境限制**：实测环境 LLM 未配置，最终未获得 LLM 生成的回复文本；配置 LLM 后端后应验证完整回复。

**已知问题**：无产品缺陷（LLM 回复缺失为环境限制）。

### 3.4 AgentTools（/agent-tools）

**前置条件**：已登录。

**用例 AT-1：工具清单展示**
1. 访问 `/agent-tools`。
- **预期结果**：页面标题"Agent 工具测试台"，工具列表加载 <2s，共 13 个工具（memory_recall、memory_remember、memory_forget、memory_search、memory_get_context、memory_create_table、memory_add_record 等）；协议格式显示"OpenAI Function Calling"；工具状态"可用"。
- **验证要点**：每个工具可展开查看 JSON Schema。
- **实测截图**：`./ui-manual-screenshots/batch3_agent_tools.png`

**用例 AT-2：执行 memory_recall 工具**
1. 展开 memory_recall 工具。
2. 填入参数 `query="uitest3_测试记忆召回"`。
3. 点击执行。
- **预期结果**：显示"执行中..."→ 执行成功，返回 JSON 结果（实测返回 8 条相关记忆），正确显示记忆内容、相关度、重要度。
- **验证要点**：执行完成后"复制结果"按钮从 disabled 变为可用。
- **性能注记**：实测执行约 30s——含 ChromaDB embedding 模型冷启动，属已知性能特征而非缺陷（见 §5.1-②）；预热后显著变快。

**用例 AT-3：工具列表刷新**
1. 点击刷新按钮。
- **预期结果**：工具列表重新加载成功，仍为 13 个工具。
- **验证要点**：刷新过程无报错。

**已知问题**：无（首次执行长耗时为 embedding 冷启动特征）。

### 3.5 Variables（/variables）

**前置条件**：已登录。

**用例 V-1：变量列表展示**
1. 访问 `/variables`。
- **预期结果**：变量列表正常渲染，实测含 3 个遗留示例变量：`uitest1_user_name`、`uitest1_user_age`、`uitest1_user_email`。
- **验证要点**：每行可见编辑/删除等操作按钮。
- **实测截图**：`./ui-manual-screenshots/batch1_variables.png`

**用例 V-2：创建变量**
1. 点击新建，填写变量名（建议带测试前缀，如 `uitest_xxx`）与值。
2. 提交。
- **预期结果**：创建成功，新变量出现在列表中（批次1 实测连续创建 3 个变量均成功）。
- **验证要点**：刷新页面后变量仍在（数据持久化）；Dashboard 变量计数同步 +1。

**用例 V-3：数据持久化验证**
1. 重启浏览器（或退出重新登录）后再访问 `/variables`。
- **预期结果**：此前创建的变量完整保留。
- **验证要点**：值、名称无丢失或乱码。

**已知问题**：无产品缺陷。附注：自动化脚本直接改 DOM 无法驱动表单（React 受控组件），人工操作正常（见 §5.3）。

### 3.6 Extraction（/extraction）

**前置条件**：已登录；实际执行抽取需配置 LLM 后端。

**用例 E-1：页面加载与 Tab 结构**
1. 访问 `/extraction`。
- **预期结果**：页面正常加载，含 4 个功能 Tab。
- **验证要点**：各 Tab 均可切换，无空白页。
- **实测截图**：`./ui-manual-screenshots/batch1_extraction.png`

**用例 E-2：抽取输入框可用性**
1. 在输入框中输入待抽取文本。
- **预期结果**：输入框正常接收文本。
- **验证要点**：多行文本、中文输入正常。

**用例 E-3：抽取执行（需 LLM）**
1. 输入文本后点击执行抽取。
- **预期结果**：LLM 已配置时应返回抽取出的变量/片段；**实测环境 LLM 未配置，未实际执行 LLM 调用**，功能框架完整。
- **验证要点**：LLM 未配置时页面应有降级表现而非崩溃。
- **前置条件注记**：完整验证本用例必须先在 System 页配置可用 LLM 后端。

**已知问题**：无产品缺陷；抽取执行依赖 LLM 配置（环境限制，见 §5.2）。

### 3.7 Tables（/tables）

**前置条件**：已登录。

**用例 T-1：表列表展示**
1. 访问 `/tables`。
- **预期结果**：表列表正常显示，实测环境含 2 个表（其中 `uitest1_contacts` 为遗留示例表）。
- **验证要点**：可见表名与管理操作入口。
- **实测截图**：`./ui-manual-screenshots/batch1_tables.png`

**用例 T-2：创建记忆表**
1. 点击创建表，定义表名（如 `uitest_contacts`）与字段 schema。
2. 提交。
- **预期结果**：创建成功，新表出现在列表中（批次1 实测创建 `uitest1_contacts` 成功）。
- **验证要点**：表结构与定义一致。

**用例 T-3：插入与查看记录**
1. 进入表详情，插入记录（批次1 实测插入 2 条记录）。
2. 查看记录列表。
- **预期结果**：记录插入成功并正确显示。
- **验证要点**：字段值展示与输入一致；记录数正确。

**已知问题**：无。

### 3.8 Fragments（/fragments）

**前置条件**：已登录。

**用例 F-1：片段列表与分页**
1. 访问 `/fragments`。
- **预期结果**：片段列表正常显示（实测 175 条，含 2 条 `uitest1_` 前缀遗留片段），分页 10 条/页，支持翻页。
- **验证要点**：每行可见编辑/删除按钮；"高级过滤"可展开。
- **实测截图**：`./ui-manual-screenshots/batch1_fragments.png`

**用例 F-2：新建片段对话框**
1. 点击新建片段。
- **预期结果**：对话框正常打开，包含：类型选择器（combobox，**必填**）、内容输入框（**必填**）、重要性调整（spinbutton，可选）、TTL 设置（spinbutton，可选）、取消/确定按钮。
- **验证要点**：必填字段带 required 标记；批次1 实测通过该表单成功创建 2 条新片段。

**用例 F-3：语义搜索选项卡**
1. 切换到语义搜索选项卡，输入查询词搜索。
- **预期结果**：选项卡可访问，返回语义相关片段。
- **验证要点**：首次搜索可能因 embedding 冷启动偏慢（见 §5.1-②）。

**已知问题**：无产品缺陷。附注：自动化 fill 工具对该表单响应超时属自动化限制，人工输入正常（见 §5.3）。

### 3.9 Recall（/recall）

**前置条件**：已登录；有片段数据。

**用例 R-1：页面加载**
1. 访问 `/recall`。
- **预期结果**：自动召回页正常渲染；未提交查询时结果区为空（属预期）。
- **实测截图**：`./ui-manual-screenshots/batch2_recall.png`

**用例 R-2：召回查询执行**
1. 输入查询上下文，提交召回。
- **预期结果**：返回相关记忆列表；实测响应时间 1.6–3.7s。
- **验证要点**：结果按相关度排列；首次查询偏慢为 embedding 冷启动（见 §5.1-②），建议先执行一次预热查询。

**用例 R-3：表单校验**
1. 不输入内容直接提交/输入后提交。
- **预期结果**：表单校验正常通过实测（批次2 记录"表单验证通过✅"）。
- **验证要点**：空输入有合理拦截或提示。

**已知问题**：无缺陷；首次响应 1.8~7s 区间为冷启动性能特征。

### 3.10 LongTerm（/long-term）

**前置条件**：已登录。

**用例 LT-1：页面加载与 UI 完整性**
1. 访问 `/long-term`。
- **预期结果**：页面正常渲染，长期记忆管理 UI 完整（列表、新建入口等均可见）。
- **实测截图**：`./ui-manual-screenshots/batch2_long_term.png`

**用例 LT-2：创建长期记忆（当前受阻，回归重点）**
1. 通过 UI 提交创建长期记忆。
- **预期结果（当前实际）**：创建失败——后端 `POST /api/v1/memory/long-term` 返回 **404**。
- **验证要点**：这是**已确认的产品缺陷**（见 §5.1-①）；修复后本用例应回归为创建成功。

**用例 LT-3：既有数据浏览**
1. 浏览已有长期记忆条目（如有）。
- **预期结果**：列表/详情展示正常，无渲染错误。
- **验证要点**：页面其余功能不受创建缺陷影响。

**已知问题**：⚠️ 创建 API 404（UI 完整但创建功能不可用），见 §5.1-①。

### 3.11 Observability（/observability）

**前置条件**：已登录。

**用例 O-1：仪表盘选项卡统计**
1. 访问 `/observability`。
- **预期结果**：页面标题"观测中心"，加载 <3s，共 7 个选项卡。仪表盘选项卡显示统计（实测值）：记忆总量 182、活跃记忆 178、日新增 2、召回命中率 40.0%、存储占用 54.97 MB、LLM Token（24h）0、延迟 P50 65.2ms、延迟 P99 587.2ms、质量均分 0.000。
- **验证要点**：记忆类型分布图表正常渲染。
- **实测截图**：`./ui-manual-screenshots/batch3_observability.png`

**用例 O-2：最近追踪事件表**
1. 查看仪表盘下方追踪事件表。
- **预期结果**：显示 10+ 条追踪记录（实测）。
- **验证要点**：执行召回/搜索操作后应产生新追踪记录。

**用例 O-3：其余选项卡遍历**
1. 依次切换：指标历史、记忆追踪、追踪事件、抽取触发、性能指标、质量评估。
- **预期结果**：全部可访问；"指标历史"实测显示空态"暂无指标历史数据"（属正常空态而非错误）。
- **验证要点**：空态文案友好，无报错。

**已知问题**：无。

### 3.12 Lifecycle（/lifecycle）

**前置条件**：已登录。

**用例 LC-1：页面加载与 Tab 结构**
1. 访问 `/lifecycle`。
- **预期结果**：生命周期页正常渲染，6 个 Tab 完整可切换。
- **实测截图**：`./ui-manual-screenshots/batch2_lifecycle.png`

**用例 LC-2：半衰期（衰减）配置查看**
1. 进入衰减相关 Tab。
- **预期结果**：半衰期配置项可见（批次2 实测确认）。
- **验证要点**：配置值展示正常。

**用例 LC-3：各 Tab 内容渲染**
1. 遍历 6 个 Tab。
- **预期结果**：均正常渲染，无空白或报错页。
- **验证要点**：列表/配置区数据加载正常。

**已知问题**：无。

### 3.13 GraphMemory（/graph-memory）

**前置条件**：已登录；有图谱数据（实测环境：7 个实体、4 个关系）。

**用例 G-1：图谱渲染**
1. 访问 `/graph-memory`。
- **预期结果**：力导向图正常渲染，显示 7 实体 4 关系（实测值）。
- **实测截图**：`./ui-manual-screenshots/batch2_graph_memory.png`

**用例 G-2：布局切换**
1. 依次切换 3 种布局。
- **预期结果**：3 种布局均正常切换与重绘（批次2 实测✅）。
- **验证要点**：切换后节点无丢失。

**用例 G-3：实体/关系数据一致性**
1. 对照后端图谱数据核对页面显示的实体与关系数量。
- **预期结果**：数量一致（7 实体 + 4 关系）。
- **验证要点**：节点标签、关系连线正确。

**已知问题**：无。

### 3.14 HybridSearch（/hybrid-search）

**前置条件**：已登录；有片段数据。

**用例 H-1：页面加载**
1. 访问 `/hybrid-search`。
- **预期结果**：混合搜索页正常渲染，查询输入与选项可见。
- **实测截图**：`./ui-manual-screenshots/batch2_hybrid_search.png`

**用例 H-2：中文搜索**
1. 输入中文查询词，执行搜索。
- **预期结果**：返回相关结果（批次2 实测返回 2 个结果，中文支持✅）。
- **验证要点**：结果含相关度/得分信息。

**用例 H-3：英文搜索（多语言）**
1. 输入英文查询词，执行搜索。
- **预期结果**：英文查询同样正常返回（批次2 实测多语言支持✅）。
- **验证要点**：首次搜索偏慢为 embedding 冷启动（见 §5.1-②）。

**已知问题**：无缺陷；首次搜索 1.8~7s 为冷启动性能特征。

### 3.15 Playground（/playground）

**前置条件**：已登录。

**用例 P-1：页面加载与模拟器清单**
1. 访问 `/playground`。
- **预期结果**：记忆调试工具页正常渲染，4 个模拟器（注入/召回/衰减/演化方向）入口完整，交互就绪（批次2 实测✅）。
- **实测截图**：`./ui-manual-screenshots/batch2_playground.png`

**用例 P-2：模拟器切换**
1. 依次点击 4 个模拟器。
- **预期结果**：各模拟器面板正常切换渲染。
- **验证要点**：无空白面板或控制台报错。

**用例 P-3：模拟器交互控件**
1. 检查各模拟器的输入/参数控件。
- **预期结果**：控件可交互（批次2 实测"交互就绪"）。
- **验证要点**：参数输入正常接收。

**已知问题**：无。

### 3.16 System（/system）

**前置条件**：已登录。

**用例 S-1：LLM 运行状态选项卡**
1. 访问 `/system`。
- **预期结果**：页面标题"系统集成"；LLM 运行状态选项卡显示状态指示 healthy；初始信息区显示空态"暂无数据，点击刷新获取状态"。
- **实测截图**：`./ui-manual-screenshots/batch3_system.png`

**用例 S-2：可用后端列表（空态）**
1. 查看可用后端区域，点击刷新。
- **预期结果**：实测环境显示"暂无可用后端，点击刷新"（LLM 未配置，属正常空态）。
- **验证要点**：配置 LLM 后端后此处应出现后端条目。

**用例 S-3：其余选项卡遍历**
1. 依次切换：LLM 后端配置、插件管理、性能监控、安全检查。
- **预期结果**：4 个选项卡均可访问，正常渲染（批次3 实测✅）。
- **验证要点**：无报错、无空白页。

**已知问题**：无产品缺陷；"暂无可用后端"为 LLM 未配置的环境状态（见 §5.2）。

### 3.17 WorkspaceSettings（/workspace/settings）

**前置条件**：已登录。

**用例 W-1：Workspace 管理列表**
1. 访问 `/workspace/settings`。
- **预期结果**：页面标题"Workspace 设置"；工作区列表正常显示（实测：个人工作区 admin（user-3，owner 角色）、团队工作区 Test Team（test-team-1，owner 角色））。
- **实测截图**：`./ui-manual-screenshots/batch3_workspace_settings.png`

**用例 W-2：API Key 管理选项卡**
1. 切换到 API Key 管理选项卡。
- **预期结果**：选项卡可访问，正常渲染（批次3 实测✅）。
- **验证要点**：Key 列表/创建入口可见。

**用例 W-3：创建 Workspace 入口**
1. 检查"创建 Workspace"按钮。
- **预期结果**：按钮可见可点击。
- **验证要点**：点击后弹出创建表单。

**已知问题**：无阻断性缺陷（批次3 功能矩阵将新建/编辑标记为 ⚠️ 存在 UI 交互问题，系自动化工具表单交互限制，人工操作请按 §5.3 说明正常验证）。

---

## 4. 端到端流程验证

跨页记忆闭环流程：**创建变量 → 创建片段 → 混合搜索 → AgentChat 对话验证召回**。以下按批次3实测报告如实记录各步结果。

| 步骤 | 操作 | 实测结果 | 说明 |
|------|------|---------|------|
| 1 | 创建变量（/variables） | ⚠️ 批次3 未完成（测试导航失误 + 自动化表单限制） | 批次3 误用了不存在的 `/memory/variables` 路径（正确路由为 `/variables`，属测试执行失误而非产品缺陷）。**批次1 已在 /variables 成功创建 3 个变量并持久化**，该能力已验证可用 |
| 2 | 创建记忆片段（/fragments） | ✓ 在片段列表中验证通过 | 100+ 条片段显示正常，含 `uitest1_` 前缀历史测试数据（批次1 成功新建 2 条）；批次3 自动化脚本填表超时属工具限制（§5.3） |
| 3 | 混合搜索验证 | ✓ 成功 | 通过 AgentTools 的 memory_recall 工具完成语义搜索，返回 8 条相关记忆 |
| 4 | AgentChat 对话验证召回 | ✓ 部分成功 | 消息发送成功；记忆自动召回成功，显示"本次回复参考了 4 条记忆"；LLM 生成回复因环境限制（LLM 未配置）未获得最终回复文本 |

**结论**：记忆闭环的核心链路（数据写入 → 检索召回 → 对话注入）已验证打通；唯一未闭环的环节是 LLM 生成回复，属环境限制。配置 LLM 后端后建议完整回归本流程。

---

## 5. 已知问题与环境差异

### 5.1 缺陷与性能特征清单

| # | 类别 | 描述 | 影响 | 建议 |
|---|------|------|------|------|
| ① | **缺陷** | LongTerm 创建 API `POST /api/v1/memory/long-term` 返回 404 | /long-term 页 UI 完整但创建功能不可用 | 修复后端路由后回归用例 LT-2 |
| ② | **性能特征（非缺陷）** | embedding 模型冷启动导致召回/搜索**首次**响应 1.8~7s（AgentTools 工具首次执行含冷启动实测约 30s） | Recall / HybridSearch / Fragments 语义搜索 / AgentTools 首次操作偏慢 | 测试前先执行一次任意召回查询进行预热；后续响应恢复正常（实测 1.6–3.7s） |
| ③ | **环境限制（非缺陷）** | LLM 未配置时 AgentChat 无法生成回复、Extraction 无法实际执行抽取（均降级不崩溃） | AC-3、E-3 无法完整验证 | 前置条件：在 System 页配置可用 LLM 后端后再执行相关用例 |

### 5.2 LLM 依赖降级说明

实测环境未配置 LLM 后端（System 页显示"暂无可用后端"）。受影响功能及降级表现：

- **AgentChat**：消息发送、会话管理、记忆召回全部正常；仅最终 LLM 回复文本缺失（停留在处理中）。
- **Extraction**：页面与 4 个 Tab 框架完整；实际抽取不执行。
- **System**：LLM 状态 healthy，但可用后端列表为空态。

以上均为**优雅降级**而非崩溃，符合预期。需要完整验证 LLM 相关功能时，先在 System → LLM 后端配置中接入可用后端。

### 5.3 自动化测试限制附注（非产品缺陷，人工操作不受影响）

以下现象曾在实测过程中被初判为缺陷，经复核确认均为**浏览器自动化工具的限制**，本手册面向人工操作者，人工测试时不会遇到：

1. **React 受控表单对直接 DOM 赋值不响应**：自动化脚本直接改 `input.value` 不会触发 React 状态更新，需使用 `nativeInputValueSetter` + 合成事件注入；使用正确注入方式复测后表单工作正常。人工键盘输入完全正常。
2. **"/memory/variables 路由跳转异常"为误报**：该路由本就不存在（App.tsx 中变量页路由为 `/variables`），属测试导航失误，非 React Router 配置问题。
3. **"表单输入响应缓慢"为误报**：自动化 fill 工具对新建片段等对话框响应超时（5s），同属自动化事件注入限制，人工输入无此现象。

### 5.4 端口与账号注意事项

- **账号**：`admin` / `admin123`。README 所载 `Admin123!` 有误（经数据库哈希验证）；若登录失败请优先核对是否用错了 README 口径的密码。
- **端口冲突**：启动前用 `lsof -i :8000` / `lsof -i :5173` 检查占用；后端重启会重新生成临时 JWT 密钥导致已登录 token 失效，测试期间避免重启后端。
- **数据源**：务必使用 `backend/agent_memory.db`（根目录同名文件是空壳，users 表为空无法登录）；隔离测试按 §1.2 复制数据后以隔离目录为 CWD 启动。
- **Redis**：未启动本地 Redis 时自动回退 fakeredis，功能测试不受影响。

---

## 附录：实测截图索引（21 张）

| 截图 | 对应页面/场景 |
|------|--------------|
| `./ui-manual-screenshots/batch1_login_initial.png` | 登录页初始状态 |
| `./ui-manual-screenshots/batch1_login_filled.png` | 登录表单填写状态 |
| `./ui-manual-screenshots/batch1_login_validation_error.png` | 登录校验错误提示 |
| `./ui-manual-screenshots/batch1_login_correct_filled.png` | 正确凭据填写 |
| `./ui-manual-screenshots/batch1_login.png` | /login 全页（1440x900） |
| `./ui-manual-screenshots/batch1_dashboard.png` | / 仪表盘全页 |
| `./ui-manual-screenshots/batch1_variables.png` | /variables 全页 |
| `./ui-manual-screenshots/batch1_extraction.png` | /extraction 全页 |
| `./ui-manual-screenshots/batch1_fragments.png` | /fragments 全页 |
| `./ui-manual-screenshots/batch1_tables.png` | /tables 全页 |
| `./ui-manual-screenshots/batch2_recall.png` | /recall 全页 |
| `./ui-manual-screenshots/batch2_hybrid_search.png` | /hybrid-search 全页 |
| `./ui-manual-screenshots/batch2_graph_memory.png` | /graph-memory 全页（7 实体 4 关系） |
| `./ui-manual-screenshots/batch2_long_term.png` | /long-term 全页 |
| `./ui-manual-screenshots/batch2_lifecycle.png` | /lifecycle 全页 |
| `./ui-manual-screenshots/batch2_playground.png` | /playground 全页 |
| `./ui-manual-screenshots/batch3_agent_chat.png` | /agent-chat 全页 |
| `./ui-manual-screenshots/batch3_agent_tools.png` | /agent-tools 全页 |
| `./ui-manual-screenshots/batch3_observability.png` | /observability 全页 |
| `./ui-manual-screenshots/batch3_system.png` | /system 全页 |
| `./ui-manual-screenshots/batch3_workspace_settings.png` | /workspace/settings 全页 |
