# Agent Memory System — Code Wiki

> 版本：v0.3.0 ｜ 文档基于源码分析生成
>
> 本文档是对 Agent Memory System 仓库的结构化代码百科，涵盖项目整体架构、主要模块职责、关键类与函数说明、依赖关系以及项目运行方式。

---

## 目录

1. [项目概述](#1-项目概述)
2. [整体架构](#2-整体架构)
3. [仓库结构与 Monorepo 布局](#3-仓库结构与-monorepo-布局)
4. [后端架构（backend/）](#4-后端架构backend)
   - 4.1 [应用入口与中间件](#41-应用入口与中间件)
   - 4.2 [核心基础设施（app/core/）](#42-核心基础设施appcore)
   - 4.3 [API 路由层（app/api/）](#43-api-路由层appapi)
   - 4.4 [业务服务层（app/services/）](#44-业务服务层appservices)
   - 4.5 [ORM 模型（app/models/）](#45-orm-模型appmodels)
   - 4.6 [MCP Server](#46-mcp-server)
   - 4.7 [基准测试（app/benchmarks/）](#47-基准测试appbenchmarks)
5. [核心包（packages/core/）](#5-核心包packagescore)
   - 5.1 [MemoryEngine 引擎](#51-memoryengine-引擎)
   - 5.2 [12 个业务模块](#52-12-个业务模块)
   - 5.3 [存储抽象层（store/）](#53-存储抽象层store)
   - 5.4 [配置与事件](#54-配置与事件)
6. [SDK 生态](#6-sdk-生态)
   - 6.1 [Python SDK](#61-python-sdk)
   - 6.2 [TypeScript SDK](#62-typescript-sdk)
7. [前端架构（frontend/）](#7-前端架构frontend)
8. [数据模型与存储设计](#8-数据模型与存储设计)
9. [依赖关系](#9-依赖关系)
10. [项目运行方式](#10-项目运行方式)
11. [测试体系](#11-测试体系)
12. [部署与运维](#12-部署与运维)

---

## 1. 项目概述

**Agent Memory System** 是一个为 AI Agent 提供完整记忆管理能力的系统，让 Agent 能够像人类一样"记住"用户偏好、事实、计划和会话历史，并在后续对话中智能召回相关记忆。

### 核心能力

| 能力 | 说明 |
|------|------|
| 记忆变量（Variables） | 轻量级 KV 存储，如 `user_name: "鑫海"`，支持 session 作用域和 TTL |
| 记忆表（Tables） | 动态 Schema 的结构化数据存储，支持 NL2SQL 自然语言查询 |
| 记忆片段（Fragments） | 带向量嵌入的语义化记忆，支持 TTL、重要性评分、语义搜索 |
| 自动召回（Recall） | 基于语义相似性 + BM25 + 实体 + 时间衰减的混合智能检索 |
| 知识图谱（Graph） | 实体/关系管理、图遍历、时序追踪、矛盾检测与演变 |
| 长期记忆（Long-Term） | 版本控制、反馈机制、权重调整、自我改进 |
| 记忆生命周期（Lifecycle） | 差异化半衰期、冷热分层、软删除/恢复、自动归档、智能遗忘 |
| Agent 对话 | 带记忆上下文的 LLM 对话、工具调用、流式输出 |
| 可观测性 | Prometheus 指标、OpenTelemetry 链路追踪、质量评估 |
| MCP Server | 暴露 10 个工具供 Claude Desktop / Cursor 等 MCP 客户端调用 |

### 技术栈

| 层级 | 技术 |
|------|------|
| 后端框架 | FastAPI + Uvicorn（Python 3.13） |
| 前端框架 | React 19 + TypeScript + Vite |
| UI 组件 | Ant Design 6 |
| 状态管理 | Zustand（客户端）+ TanStack React Query（服务端） |
| 关系数据库 | SQLite（开发）/ PostgreSQL（生产） |
| 向量数据库 | ChromaDB（默认）/ Milvus |
| 缓存 | Redis / FakeRedis |
| 认证 | JWT + PBKDF2 |
| 监控 | Prometheus + Grafana + OpenTelemetry + Jaeger |

---

## 2. 整体架构

```
┌─────────────────────────────────────────────────────────────────┐
│                     前端 (React + TypeScript + Vite)             │
│              http://localhost:5173 (开发) / :3000 (Docker)        │
│   Zustand stores │ React Query hooks │ Axios + SSE fetch         │
└────────────────────────────┬────────────────────────────────────┘
                             │ REST API (/api/v1) + SSE 流
┌────────────────────────────┴────────────────────────────────────┐
│                   后端 (FastAPI + Uvicorn)                       │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────┐ │
│  │ API 路由  │ │ 核心基础设施│ │ 业务服务  │ │ MCP Server│ │ 基准测试│ │
│  │ (app/api)│ │(app/core) │ │(app/services)│ │          │ │        │ │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘ └────────┘ │
│       │            │            │            │                    │
│       └────────────┴────────────┴────────────┘                    │
│                          中间件链                                  │
│   CORS → Prometheus → 限流 → RequestID → 版本头 → 路由 → 异常处理   │
└───────┬────────────┬──────────────┬───────────────┬──────────────┘
        │            │              │               │
  ┌─────▼─────┐ ┌────▼─────┐ ┌─────▼─────┐ ┌───────▼───────┐
  │ SQLite /  │ │  Redis / │ │ ChromaDB  │ │  OTel → Jaeger │
  │ PostgreSQL│ │ FakeRedis│ │ (Vectors) │ │  Prometheus    │
  └───────────┘ └──────────┘ └───────────┘ └───────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                     核心包 (agent-memory-core)                    │
│         框架无关的纯 Python 记忆逻辑库（可嵌入式使用）              │
│  MemoryEngine → 12 模块 + 3 存储抽象（Relational/Vector/Cache）   │
└─────────────────────────────────────────────────────────────────┘

┌──────────────────────────┐  ┌──────────────────────────────────┐
│   Python SDK             │  │   TypeScript SDK                  │
│   HTTP + Embedded 双模式  │  │   零运行时依赖，原生 fetch         │
│   + LangChain + MCP 集成  │  │   + 9 个 API 子模块               │
└──────────────────────────┘  └──────────────────────────────────┘
```

### 分层设计理念

系统采用**五层架构**，自下而上：

1. **存储抽象层**：通过 ABC 接口屏蔽 SQLite/PostgreSQL、ChromaDB/Milvus、Redis/FakeRedis 的差异
2. **核心逻辑层**（`packages/core`）：框架无关的 MemoryEngine，组合 12 个业务模块
3. **业务服务层**（`app/services`）：30 个服务，封装核心包能力并扩展（LLM、Agent、安全等）
4. **API 路由层**（`app/api`）：FastAPI 路由，标准化响应信封 + 统一异常处理
5. **客户端层**：前端 + Python SDK + TypeScript SDK + MCP 客户端

---

## 3. 仓库结构与 Monorepo 布局

```
agent-memory-system/
├── backend/                    # 后端服务（FastAPI）
│   ├── app/
│   │   ├── api/                # API 路由层（21 个路由文件）
│   │   ├── core/               # 核心基础设施（配置/认证/缓存/事件总线等）
│   │   │   └── store/          # 存储抽象与实现
│   │   ├── services/           # 业务服务层（30 个服务文件）
│   │   ├── models/             # ORM 模型（SQLAlchemy 2.0）
│   │   ├── integrations/       # 集成层（LangChain / MCP 兼容）
│   │   ├── benchmarks/         # LongMemEval 基准测试
│   │   ├── main.py             # 应用入口
│   │   └── mcp_server.py       # MCP Server 主模块
│   ├── tests/                  # 测试套件（20+ 测试文件）
│   ├── alembic/                # 数据库迁移
│   ├── pyproject.toml          # Poetry 项目配置
│   ├── requirements.txt        # Docker 构建依赖
│   └── pytest.ini              # 测试配置
├── packages/
│   └── core/                   # 框架无关核心包（agent-memory-core）
│       ├── src/agent_memory_core/
│       │   ├── engine.py       # MemoryEngine 主类
│       │   ├── config.py       # CoreConfig / ServerConfig
│       │   ├── events.py       # 事件系统
│       │   ├── models/         # 数据模型
│       │   ├── modules/        # 12 个业务模块
│       │   └── store/          # 存储抽象层（base/factory/sqlite/chroma/redis/null）
│       └── pyproject.toml
├── sdk-python/                 # Python SDK（完整实现）
│   ├── src/agent_memory/
│   │   ├── client.py           # 同步客户端
│   │   ├── async_client.py     # 异步客户端
│   │   ├── transport/          # 传输层（http / embedded）
│   │   ├── api/                # API 子模块（7 个）
│   │   ├── models/             # 数据模型
│   │   ├── integrations/       # LangChain / MCP 集成
│   │   └── exceptions.py
│   └── examples/quickstart.py
├── sdk-typescript/             # TypeScript SDK（完整实现）
│   └── src/
│       ├── client.ts           # 主客户端 + 9 个 API 子模块
│       ├── transport.ts        # 传输层
│       ├── types.ts            # 类型定义
│       └── errors.ts           # 错误层级
├── frontend/                   # 前端（React + TypeScript）
│   └── src/
│       ├── pages/              # 17 个页面
│       ├── components/         # 12 个组件
│       ├── stores/             # Zustand 状态管理
│       ├── hooks/              # React Query hooks
│       └── services/api.ts     # API 客户端
├── deploy/                     # 部署配置（Grafana / Prometheus / OTel）
├── docker/                     # Docker Compose 辅助文件
├── k8s/                        # Kubernetes 部署
├── docs/                       # 项目文档
├── Dockerfile                  # 多阶段构建
├── docker-compose.yml          # 开发环境
└── docker-compose.prod.yml     # 生产环境
```

> **注意**：`packages/sdk-python/` 和 `packages/sdk-typescript/` 为脚手架占位（stubs），实际完整实现在根目录的 `sdk-python/` 和 `sdk-typescript/`。

---

## 4. 后端架构（backend/）

### 4.1 应用入口与中间件

**文件**：[main.py](file:///Users/howdy/pm/agent-memory-system/backend/app/main.py)

应用入口负责 FastAPI 实例创建、中间件注册、路由挂载、生命周期管理和 MCP Server 挂载。

#### 生命周期管理（lifespan）

应用启动时按序初始化：
1. OpenTelemetry 链路追踪
2. 生命周期调度器（`start_lifecycle_scheduler`）
3. Outbox 调度器（`start_outbox_scheduler`）—— 保证 SQLite + ChromaDB 跨存储原子性
4. EventBus 启动（`event_bus.start()`）
5. Webhook worker 启动（`start_webhook_worker`）
6. Webhook 订阅 EventBus（订阅 `["*"]` 全事件）
7. LLM 重试 worker 启动（`start_retry_worker`）

关闭时逆序停止。

#### 中间件链（外 → 内）

```
CORS → Prometheus 指标 → 限流 → Request ID → 版本响应头 → 路由 → 异常处理
```

| 中间件 | 职责 |
|--------|------|
| `CORSMiddleware` | 跨域处理，默认允许 localhost:5173/4173 |
| `Prometheus Instrumentator` | 自动 HTTP 指标采集，暴露 `/metrics` |
| `rate_limit_middleware` | 基于 user_id 或 IP 的滑动窗口限流，超限返回 429 |
| `request_id_middleware` | 生成/透传 Request ID，绑定 structlog 上下文 |
| `version_headers_middleware` | 注入 `API-Version`/`API-Stability`/`Deprecation`/`Sunset` 头 |

#### 全局异常处理器

统一将异常转换为 `ErrorResponse` 格式（含 `code`/`message`/`details`/`trace_id`/`timestamp`）：

| 异常类型 | 处理方式 |
|----------|----------|
| `AppException` | 应用异常 → 对应状态码 + ErrorResponse |
| `RequestValidationError` | 请求校验错误 → 422 + ErrorResponse |
| `HTTPException` | 兼容旧异常 → 保留状态码 + ErrorResponse |
| `Exception` | 兜底 → 500 通用错误（不泄漏内部细节） |

#### 路由注册（21 个路由模块）

```python
app.include_router(health.router, prefix="/api/v1")
app.include_router(auth.router, prefix="/api/v1/auth")
app.include_router(memory_variables.router, prefix="/api/v1/memory")
app.include_router(memory_tables.router, prefix="/api/v1/memory/tables")
app.include_router(memory_fragments.router, prefix="/api/v1/memory/fragments")
app.include_router(auto_recall.router, prefix="/api/v1/memory/recall")
app.include_router(agent.router, prefix="/api/v1/agent")
app.include_router(graph_memory.router, prefix="/api/v1")
app.include_router(hybrid_search.router, prefix="/api/v1")
app.include_router(sessions.router, prefix="/api/v1/agent")
app.include_router(workspace.router, prefix="/api/v1/workspaces")
app.include_router(webhooks.router, prefix="/api/v1/webhooks")
app.include_router(events.router, prefix="/api/v1/events")
# ... 共 21 个路由模块
```

> **关键约定**：静态路由（如 `/batch`、`/search`）必须注册在动态路由（如 `/{id}`）之前，以防止路由冲突。

---

### 4.2 核心基础设施（app/core/）

| 文件 | 职责 | 关键类/函数 |
|------|------|-------------|
| [config.py](file:///Users/howdy/pm/agent-memory-system/backend/app/core/config.py) | 集中配置（pydantic-settings） | `Settings`、`get_settings()` |
| [auth.py](file:///Users/howdy/pm/agent-memory-system/backend/app/core/auth.py) | JWT 认证与密码哈希 | `create_access_token`、`decode_access_token`、`hash_password`、`verify_password` |
| [db_client.py](file:///Users/howdy/pm/agent-memory-system/backend/app/core/db_client.py) | 数据库客户端（SQLite） | `DBClient` |
| [pg_client.py](file:///Users/howdy/pm/agent-memory-system/backend/app/core/pg_client.py) | PostgreSQL 客户端 | `PostgresClient`（翻译 SQLite 方言 SQL） |
| [redis_client.py](file:///Users/howdy/pm/agent-memory-system/backend/app/core/redis_client.py) | Redis 客户端 | `get_redis()` |
| [chromadb_client.py](file:///Users/howdy/pm/agent-memory-system/backend/app/core/chromadb_client.py) | ChromaDB 客户端 | `ChromaDBClient` |
| [milvus_client.py](file:///Users/howdy/pm/agent-memory-system/backend/app/core/milvus_client.py) | Milvus 客户端 | `MilvusClient` |
| [cache.py](file:///Users/howdy/pm/agent-memory-system/backend/app/core/cache.py) | TTL 缓存装饰器 | `@ttl_cache(ttl, key_prefix)`（Redis + 内存双层） |
| [event_bus.py](file:///Users/howdy/pm/agent-memory-system/backend/app/core/event_bus.py) | 事件总线 | `EventBus`（ABC）、`InMemoryEventBus`、`RedisEventBus`、`get_event_bus()` |
| [errors.py](file:///Users/howdy/pm/agent-memory-system/backend/app/core/errors.py) | 统一错误处理 | `ErrorCode`、`ErrorResponse`、`AppException`、`handle_service_result` |
| [response.py](file:///Users/howdy/pm/agent-memory-system/backend/app/core/response.py) | 响应信封 | `ok()`、`fail()`、`paginate()` |
| [metrics.py](file:///Users/howdy/pm/agent-memory-system/backend/app/core/metrics.py) | Prometheus 指标 | `memory_operations_total`、`memory_recall_latency_seconds` 等 |
| [tracing.py](file:///Users/howdy/pm/agent-memory-system/backend/app/core/tracing.py) | OpenTelemetry 追踪 | `init_tracing()`、`get_tracer()` |
| [circuit_breaker.py](file:///Users/howdy/pm/agent-memory-system/backend/app/core/circuit_breaker.py) | 断路器模式 | `CircuitBreaker`（CLOSED→OPEN→HALF_OPEN）、`CircuitBreakerRegistry` |
| [rbac.py](file:///Users/howdy/pm/agent-memory-system/backend/app/core/rbac.py) | RBAC 权限框架 | `Perm`、`require_permission()`、`require_workspace_access()` |
| [versioning.py](file:///Users/howdy/pm/agent-memory-system/backend/app/core/versioning.py) | API 版本管理 | `API_VERSION`、`EndpointStability`、`resolve_stability()` |
| [log_setup.py](file:///Users/howdy/pm/agent-memory-system/backend/app/core/log_setup.py) | 结构化日志 | `setup_logging()`、`get_logger()`（structlog） |
| [schema_ddl.py](file:///Users/howdy/pm/agent-memory-system/backend/app/core/schema_ddl.py) | Schema 单一数据源 | `CORE_DDL`、`FTS_TABLE_DDL`、`COMPAT_ALTERS` |
| [migrations.py](file:///Users/howdy/pm/agent-memory-system/backend/app/core/migrations.py) | 数据库迁移 | `MIGRATIONS`、`run_migrations()`、`get_migration_status()` |
| [init_db.py](file:///Users/howdy/pm/agent-memory-system/backend/app/core/init_db.py) | 数据库初始化 | `init_database()` |

#### 配置项（Settings）

`Settings` 基于 `pydantic-settings`，配置来源优先级：环境变量 > `.env` 文件 > 默认值。关键配置分组：

- **数据库**：`DATABASE_URL`（默认 sqlite）、`DB_POOL_SIZE`、`DB_ECHO`
- **向量存储**：`VECTOR_BACKEND`（chroma/milvus/qdrant）、`CHROMA_PERSIST_DIR`
- **Redis/事件总线**：`REDIS_URL`、`EVENT_BUS_BACKEND`（memory/redis）
- **认证**：`JWT_SECRET_KEY`、`JWT_ALGORITHM`、`JWT_EXPIRATION_HOURS`、`PBKDF2_ITERATIONS`
- **监控**：`ENABLE_METRICS`、`ENABLE_TRACING`、`OTLP_ENDPOINT`
- **LLM**：`DEEPSEEK_API_KEY`、`DEEPSEEK_BASE_URL`、`DEEPSEEK_MODEL`、`LLM_TIMEOUT_SECONDS`
- **限流**：`RATE_LIMIT_REQUESTS`（默认 100）、`RATE_LIMIT_WINDOW_SECONDS`（默认 60）
- **MCP Server**：`MCP_ENABLED`、`MCP_TRANSPORT`、`MCP_HOST`、`MCP_PORT`

#### 错误码体系

```python
class ErrorCode(str, Enum):
    AUTH_INVALID_CREDENTIALS = "AUTH_INVALID_CREDENTIALS"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    FORBIDDEN = "FORBIDDEN"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    WEBHOOK_DELIVERY_FAILED = "WEBHOOK_DELIVERY_FAILED"
```

#### 响应信封

所有 API 响应使用标准化格式：

```python
# 成功
{"success": True, "data": ..., "error": None, "trace_id": "...", "meta": {...}}

# 分页
{"success": True, "data": [...], "meta": {"count": N, "total": M, "page": 1, "page_size": 20, "total_pages": P, "has_next": True, "has_prev": False}}

# 失败
{"success": False, "data": None, "error": "...", "code": "NOT_FOUND", "trace_id": "..."}
```

---

### 4.3 API 路由层（app/api/）

共 21 个路由文件，按功能分组：

| 路由文件 | 前缀 | 核心端点 |
|----------|------|----------|
| [health.py](file:///Users/howdy/pm/agent-memory-system/backend/app/api/health.py) | `/api/v1/health` | `GET /`（综合）、`GET /live`（存活）、`GET /ready`（就绪） |
| [auth.py](file:///Users/howdy/pm/agent-memory-system/backend/app/api/auth.py) | `/api/v1/auth` | `POST /register`、`POST /login`、`GET /me` |
| [memory_variables.py](file:///Users/howdy/pm/agent-memory-system/backend/app/api/memory_variables.py) | `/api/v1/memory` | `POST /variables`、`GET /variables`、`GET /variables/{key}`、`DELETE /variables/{key}`、`POST /extract`、`POST /render` |
| [memory_tables.py](file:///Users/howdy/pm/agent-memory-system/backend/app/api/memory_tables.py) | `/api/v1/memory/tables` | `POST /`（创建表）、`GET /`（列表）、`POST /{name}/records`、`GET /{name}/records`、`DELETE /{name}`、`POST /nl-query`（NL2SQL） |
| [memory_fragments.py](file:///Users/howdy/pm/agent-memory-system/backend/app/api/memory_fragments.py) | `/api/v1/memory/fragments` | `POST /`（创建）、`GET /`（列表）、`GET /{id}`、`PUT /{id}`、`DELETE /{id}`、`POST /search`（语义搜索） |
| [auto_recall.py](file:///Users/howdy/pm/agent-memory-system/backend/app/api/auto_recall.py) | `/api/v1/memory/recall` | `POST /`（自动召回）、`GET /config`、`PUT /config`、`GET /stats` |
| [agent.py](file:///Users/howdy/pm/agent-memory-system/backend/app/api/agent.py) | `/api/v1/agent` | `POST /chat`、`POST /chat/stream`（SSE 流式）、`GET /tools/schema` |
| [graph_memory.py](file:///Users/howdy/pm/agent-memory-system/backend/app/api/graph_memory.py) | `/api/v1` | 实体/关系 CRUD、`GET /memory/graph/entities/{id}/neighbors`（图遍历）、`POST /memory/graph/extract`（抽取）、`POST /memory/graph/query`（NL 查询） |
| [hybrid_search.py](file:///Users/howdy/pm/agent-memory-system/backend/app/api/hybrid_search.py) | `/api/v1` | `POST /hybrid/search`、`GET /hybrid/config`、`PUT /hybrid/config` |
| [long_term_memory.py](file:///Users/howdy/pm/agent-memory-system/backend/app/api/long_term_memory.py) | `/api/v1/memory/long-term` | 记忆列表、版本记录、审计日志、反馈、权重调整 |
| [memory_lifecycle.py](file:///Users/howdy/pm/agent-memory-system/backend/app/api/memory_lifecycle.py) | `/api/v1` | 生命周期统计、冷数据、软删/恢复、归档、重复检测、合并、冲突解决 |
| [memory_observability.py](file:///Users/howdy/pm/agent-memory-system/backend/app/api/memory_observability.py) | `/api/v1` | 仪表盘、指标历史、追踪事件、质量评估 |
| [smart_forgetting.py](file:///Users/howdy/pm/agent-memory-system/backend/app/api/smart_forgetting.py) | `/api/v1` | `POST /memory/forgetting/recalculate`、`GET /memory/forgetting/importance/{id}`、`GET /memory/forgetting/statistics` |
| [sessions.py](file:///Users/howdy/pm/agent-memory-system/backend/app/api/sessions.py) | `/api/v1/agent` | 会话 CRUD、消息列表、摘要生成 |
| [workspace.py](file:///Users/howdy/pm/agent-memory-system/backend/app/api/workspace.py) | `/api/v1/workspaces` | 工作空间 CRUD、成员管理、切换 |
| [webhooks.py](file:///Users/howdy/pm/agent-memory-system/backend/app/api/webhooks.py) | `/api/v1/webhooks` | Webhook CRUD、测试、投递记录 |
| [events.py](file:///Users/howdy/pm/agent-memory-system/backend/app/api/events.py) | `/api/v1/events` | 事件列表、事件类型、SSE 订阅 |
| [memory_extraction.py](file:///Users/howdy/pm/agent-memory-system/backend/app/api/memory_extraction.py) | `/api/v1/memory/extraction` | 抽取处理、批量抽取、摘要、上下文、Prompt 模板 |
| [system_integration.py](file:///Users/howdy/pm/agent-memory-system/backend/app/api/system_integration.py) | `/api/v1/system` | 健康检查、LLM 后端管理、插件、性能、安全 |
| [business_metrics.py](file:///Users/howdy/pm/agent-memory-system/backend/app/api/business_metrics.py) | `/api/v1` | 业务指标端点 |

> **关键约定**：资源创建端点返回 HTTP 201；API 路由 handler 使用空路径字符串（`@router.post("")`）而非 `"/"` 避免 POST 请求的 307 重定向。

---

### 4.4 业务服务层（app/services/）

30 个服务文件，按职能分 7 层：

#### 存储抽象层（底层，被多方依赖）

| 服务文件 | 职责 | 关键方法 |
|----------|------|----------|
| [memory_fragment_service.py](file:///Users/howdy/pm/agent-memory-system/backend/app/services/memory_fragment_service.py) | 记忆片段 CRUD + Outbox 模式 | `create_fragment`、`get_fragment`、`list_fragments`、`search_fragments_by_semantic`、`cleanup_expired_fragments`、`start_outbox_scheduler` |
| [memory_variable_service.py](file:///Users/howdy/pm/agent-memory-system/backend/app/services/memory_variable_service.py) | KV 变量管理 | `set_memory_variable`、`get_memory_variable`、`list_memory_variables`、`extract_variables_from_text`、`render_template` |
| [memory_table_service.py](file:///Users/howdy/pm/agent-memory-system/backend/app/services/memory_table_service.py) | 动态表管理 | `create_table`、`list_tables`、`add_record`、`query_records`、`delete_table` |

#### 召回与检索编排层

| 服务文件 | 职责 | 关键方法 |
|----------|------|----------|
| [recall_engine.py](file:///Users/howdy/pm/agent-memory-system/backend/app/services/recall_engine.py) | 统一召回引擎 | `RecallEngine.recall()`（语义+混合+生命周期+图谱融合） |
| [auto_recall_service.py](file:///Users/howdy/pm/agent-memory-system/backend/app/services/auto_recall_service.py) | 自动召回编排 | `search_relevant_memories`、`generate_summary`、配置管理 |
| [advanced_recall.py](file:///Users/howdy/pm/agent-memory-system/backend/app/services/advanced_recall.py) | P0/P1 优化层 | `decompose_session`（会话分解）、`generate_search_keys`（多键索引）、`expand_query_with_time`（时间感知扩展）、多会话聚合 |
| [hybrid_search_service.py](file:///Users/howdy/pm/agent-memory-system/backend/app/services/hybrid_search_service.py) | 混合检索 | `hybrid_search()`（语义+BM25+实体+时间衰减融合排序）、`rerank_with_llm()` |
| [context_compressor.py](file:///Users/howdy/pm/agent-memory-system/backend/app/services/context_compressor.py) | 上下文压缩与记忆注入 | `ContextCompressor.build_context()`、`MemoryValueScorer`、`EntityGraphTraverser`、`estimate_tokens()` |

**混合搜索融合公式**：
```
final_score = α * semantic + β * bm25 + γ * entity_boost + δ * recency
# 默认权重: α=0.35, β=0.30, γ=0.20, δ=0.15
```

#### 生命周期与遗忘层

| 服务文件 | 职责 | 关键方法 |
|----------|------|----------|
| [memory_lifecycle_service.py](file:///Users/howdy/pm/agent-memory-system/backend/app/services/memory_lifecycle_service.py) | 生命周期管理 | `calculate_decay_score`（半衰期衰减）、`mark_cold`、`soft_delete`、`restore_memory`、`auto_archive_cold_memories`、`find_duplicates`、`merge_memories`、`detect_conflicts` |
| [smart_forgetting_service.py](file:///Users/howdy/pm/agent-memory-system/backend/app/services/smart_forgetting_service.py) | 智能遗忘（R-07） | `compute_importance_score`（多因子评分）、`recalculate_importance`、`get_importance_breakdown`、`get_forgetting_statistics` |
| [contradiction_service.py](file:///Users/howdy/pm/agent-memory-system/backend/app/services/contradiction_service.py) | 矛盾检测与演变 | `detect_contradiction` |
| [long_term_memory_service.py](file:///Users/howdy/pm/agent-memory-system/backend/app/services/long_term_memory_service.py) | 长期记忆版本控制 | `record_version`、版本历史、回滚、审计日志、反馈、权重调整 |

**重要性评分公式（R-07 智能遗忘）**：
```
importance = 0.35 * recall_frequency + 0.25 * time_decay + 0.30 * evidence_strength + 0.10 * contradiction_penalty
```

**半衰期配置**：
```
info:       永久（无衰减）
plan:       90 天
preference: 1 天
event:      30 天
procedure:  180 天
```

#### LLM 与抽取层

| 服务文件 | 职责 | 关键方法 |
|----------|------|----------|
| [llm_backend_service.py](file:///Users/howdy/pm/agent-memory-system/backend/app/services/llm_backend_service.py) | LLM 适配层（多 Provider） | `llm_chat()`、`llm_chat_stream()`（含熔断/回退） |
| [llm_extraction_service.py](file:///Users/howdy/pm/agent-memory-system/backend/app/services/llm_extraction_service.py) | LLM 记忆抽取 | `llm_extract_memories()`（抽取变量/事实/偏好/计划 + 去重存储） |
| [llm_retry_queue.py](file:///Users/howdy/pm/agent-memory-system/backend/app/services/llm_retry_queue.py) | 异步重试队列 | `enqueue_retry()`、`start_retry_worker()`（指数退避） |
| [memory_extraction_service.py](file:///Users/howdy/pm/agent-memory-system/backend/app/services/memory_extraction_service.py) | 记忆抽取编排 | `process_extraction`、`batch_extract`、`generate_summary` |

#### Agent 编排层

| 服务文件 | 职责 | 关键方法 |
|----------|------|----------|
| [agent_loop.py](file:///Users/howdy/pm/agent-memory-system/backend/app/services/agent_loop.py) | 记忆感知 Agent 对话循环 | `memory_aware_chat()`、`memory_aware_chat_stream()` |
| [agent_memory_sdk.py](file:///Users/howdy/pm/agent-memory-system/backend/app/services/agent_memory_sdk.py) | 兼容层 | 委托 `agent_memory.MemoryClient` SDK |

#### 知识图谱与可观测层

| 服务文件 | 职责 | 关键方法 |
|----------|------|----------|
| [graph_memory_service.py](file:///Users/howdy/pm/agent-memory-system/backend/app/services/graph_memory_service.py) | 知识图谱 | `ensure_entity`、`add_relationship`、`get_neighbors`（BFS 遍历）、`extract_entities_from_text`、`query_graph`（NL 查询）、`detect_duplicate_entities`、`merge_entities` |
| [memory_observability_service.py](file:///Users/howdy/pm/agent-memory-system/backend/app/services/memory_observability_service.py) | 可观测性 | `record_trace_event`、`get_dashboard_stats`、`evaluate_memory_accuracy`、`update_recall_metrics` |

#### 智能问数与安全层

| 服务文件 | 职责 | 关键方法 |
|----------|------|----------|
| [natural_language_query_service.py](file:///Users/howdy/pm/agent-memory-system/backend/app/services/natural_language_query_service.py) | NL2SQL 智能问数 | `nl_to_sql()`、`execute_nl_query()` |
| [sql_safety.py](file:///Users/howdy/pm/agent-memory-system/backend/app/services/sql_safety.py) | SQL 注入防护 | `validate_sql_safety()`、`pre_check_sql()`（EXPLAIN 预检）、`is_select_only()` |
| [security_service.py](file:///Users/howdy/pm/agent-memory-system/backend/app/services/security_service.py) | 通用安全 | `get_rate_limiter()`、SQL 注入检测、XSS 检测、输入净化 |

#### 外设与支撑层

| 服务文件 | 职责 |
|----------|------|
| [webhook_service.py](file:///Users/howdy/pm/agent-memory-system/backend/app/services/webhook_service.py) | Webhook 订阅、HMAC 签发、指数退避投递 |
| [workspace_service.py](file:///Users/howdy/pm/agent-memory-system/backend/app/services/workspace_service.py) | 多租户工作区、成员角色 |
| [api_key_service.py](file:///Users/howdy/pm/agent-memory-system/backend/app/services/api_key_service.py) | API Key 管理 |
| [oauth_service.py](file:///Users/howdy/pm/agent-memory-system/backend/app/services/oauth_service.py) | OAuth 认证 |
| [performance_service.py](file:///Users/howdy/pm/agent-memory-system/backend/app/services/performance_service.py) | 性能监控 |
| [plugin_service.py](file:///Users/howdy/pm/agent-memory-system/backend/app/services/plugin_service.py) | 插件管理 |

#### 服务交互关系

服务间通过 `from app.services.X import Y` 调用，循环依赖通过**函数内延迟导入**破解：

```
记忆感知对话调用链：
agent_loop.memory_aware_chat
  ├─ context_compressor.build_context（注入历史 + 记忆）
  │    ├─ memory_variable_service.list_memory_variables
  │    ├─ hybrid_search_service.hybrid_search → memory_fragment_service.search_fragments_by_semantic
  │    ├─ memory_lifecycle_service.calculate_decay_score
  │    └─ memory_observability_service.record_trace_event
  ├─ llm_backend_service.llm_chat（含熔断/回退）
  │    └─（失败时）llm_retry_queue.enqueue_retry
  └─ llm_extraction_service.llm_extract_memories（对话后抽取）
       ├─ memory_variable_service.set_memory_variable
       ├─ memory_fragment_service.create_fragment
       │    └─（延迟）contradiction_service.detect_contradiction
       └─ memory_lifecycle_service.find_duplicates
```

**关键设计模式**：
- **Outbox Pattern**：跨存储操作（SQLite + ChromaDB）通过 `vector_outbox` 表保证原子性
- **延迟导入**：`recall_engine ↔ context_compressor`、`llm_backend ↔ llm_retry_queue` 等双向依赖通过函数内 import 化解
- **非破坏性增强**：`advanced_recall`（P1）委托标准问题给 P0 `auto_recall`，仅处理时间/多会话问题

---

### 4.5 ORM 模型（app/models/）

**文件**：[orm.py](file:///Users/howdy/pm/agent-memory-system/backend/app/models/orm.py)

使用 SQLAlchemy 2.0 声明式模型，作为 Alembic 迁移与 Store 抽象层的 schema 事实来源。共 19 个表：

| 分类 | 模型类 | 表名 | 说明 |
|------|--------|------|------|
| 基础 | `User` | users | username/email 唯一 |
| 基础 | `MemoryVariable` | memory_variables | UNIQUE(user_id, key) |
| 基础 | `MemoryTable` | memory_tables | table_schema 为 JSON |
| 基础 | `MemoryFragment` | memory_fragments | fragment_type(info/preference/plan)、lifecycle_status、importance_score |
| 生命周期 | `MemoryLifecycle` | memory_lifecycle | 复合索引(user_id, lifecycle_status) |
| 生命周期 | `MemoryDeleteLog` | memory_delete_log | 删除审计 |
| 生命周期 | `MemoryMergeLog` | memory_merge_log | 合并审计 |
| 图谱 | `GraphEntity` | graph_entities | UNIQUE(user_id, name, entity_type) |
| 图谱 | `GraphRelationship` | graph_relationships | 双外键，双时间线(valid_from/valid_to/observed_at/expired_at) |
| 图谱 | `GraphRelationshipHistory` | graph_relationship_history | 关系变更历史 |
| 观测 | `MemoryTraceEvent` | memory_trace_events | 事件追踪 |
| 观测 | `MemoryMetricsSnapshot` | memory_metrics_snapshots | 每日快照 |
| 观测 | `MemoryQualityEvaluation` | memory_quality_evaluations | 质量评估 |
| 观测 | `MemoryExtractionTrigger` | memory_extraction_triggers | 抽取触发记录 |
| 性能 | `PerformanceMetric` | performance_metrics | 三列复合索引 |
| 抽取 | `ExtractionFeedback` | extraction_feedback | rating/correction |
| 抽取 | `ExtractionPromptTemplate` | extraction_prompt_templates | UNIQUE(user_id, name) |
| 多租户 | `Organization` | organizations | plan(free/pro/enterprise) |
| 多租户 | `Workspace` | workspaces | UNIQUE(slug)，kind(personal/team) |
| 多租户 | `WorkspaceMember` | workspace_members | role(owner/admin/member/viewer) |
| 多租户 | `ApiKey` | api_keys | key_hash 唯一，scopes 为 JSON |

---

### 4.6 MCP Server

**文件**：[mcp_server.py](file:///Users/howdy/pm/agent-memory-system/backend/app/mcp_server.py)

将记忆系统核心能力暴露为 MCP（Model Context Protocol）工具，供 Claude Desktop / Cursor 等 MCP 客户端调用。

#### 传输方式

| 传输方式 | 用途 | 启动方式 |
|----------|------|----------|
| `stdio`（默认） | 子进程模式 | `python -m app.mcp_server` |
| `streamable_http` | 挂载到 FastAPI | `mount_to_app(app, path="/mcp")` |
| `sse` | 备选 | `sse_app()` |

#### 工具集（10 个）

**记忆管理（7 个，通过 SDK 调用）**：

| 工具 | 参数 | 用途 |
|------|------|------|
| `memory_recall` | `query`, `top_k=5` | 召回相关历史记忆 |
| `memory_remember` | `key`, `value`, `ttl?` | 存储 KV 变量 |
| `memory_forget` | `key` | 删除记忆变量 |
| `memory_search` | `query`, `top_k=5`, `threshold=0.3` | 语义搜索片段 |
| `memory_get_context` | （无） | 获取完整记忆上下文 |
| `memory_create_table` | `table_name`, `fields` | 创建结构化记忆表 |
| `memory_add_record` | `table_name`, `record` | 向记忆表添加记录 |

**知识图谱（3 个，直接调用 graph_memory_service）**：

| 工具 | 参数 | 用途 |
|------|------|------|
| `graph_add_entity` | `name`, `entity_type`, `aliases?`, `metadata?` | 创建/更新实体 |
| `graph_search_entities` | `query`, `entity_type?`, `limit=10` | 模糊搜索实体 |
| `graph_query_neighbors` | `entity_name`, `entity_type='person'`, `relation_type?`, `depth=1` | 查询关系网络 |

#### 辅助端点

- `GET /api/v1/mcp/tools`：查询 MCP Server 暴露的工具清单
- `GET /api/v1/mcp/status`：查询 MCP Server 状态

---

### 4.7 基准测试（app/benchmarks/）

**文件**：[benchmarks/](file:///Users/howdy/pm/agent-memory-system/backend/app/benchmarks/)

基于 LongMemEval 基准测试框架，端到端评估记忆系统效果。

| 文件 | 职责 |
|------|------|
| `runner.py` | 基准测试运行器：加载数据集 → 摄入会话 → 召回记忆 → 生成答案 → 评估正确性 |
| `evaluator.py` | 评估器：LLM Judge + 启发式评估 |
| `longmemeval_adapter.py` | LongMemEval 数据集适配器 |
| `sample_data.py` | 合成数据集（无需下载真实数据） |

**运行方式**：
```bash
# 合成数据集（无需 LLM）
python -m app.benchmarks.runner --sample

# 真实数据集
python -m app.benchmarks.runner --data path/to/longmemeval_s.json --limit 50
```

---

## 5. 核心包（packages/core/）

**包名**：`agent-memory-core`，版本 0.1.0，MIT 许可证，Python >= 3.11。

**定位**：框架无关的纯 Python 记忆逻辑库，无 HTTP/auth 依赖，可嵌入式使用或通过 Server 薄封装调用。核心依赖极简：仅 `pydantic` + `pydantic-settings`。

### 5.1 MemoryEngine 引擎

**文件**：[engine.py](file:///Users/howdy/pm/agent-memory-system/packages/core/src/agent_memory_core/engine.py)

`MemoryEngine` 是核心层统一入口，组合所有子管理器，提供高级便捷方法。

#### 构造与初始化

```python
class MemoryEngine:
    def __init__(self, relational_store, vector_store, cache_store=None,
                 event_emitter=None, llm_backend=None, config=None):
        # 注入三个 store + 事件发射器 + LLM 后端 + 配置
        # 自动 ensure_schema() + _init_managers() 装配所有子管理器

    @classmethod
    def from_config(cls, config=None):
        # 从 CoreConfig 自动创建所有 store 实例 + LLM 后端
```

#### 子管理器装配（依赖注入分层）

1. **无跨管理器依赖**：`VariableManager`、`FragmentManager`、`TableManager`、`SecurityManager`
2. **可选 LLM 依赖**：`ObservabilityManager`、`GraphManager`、`LifecycleManager`
3. **跨管理器依赖**：`RecallManager`（依赖 LifecycleManager）、`ExtractionManager`（依赖 Variable+Fragment）、`HybridSearchManager`、`ContextCompressor`（依赖 Variable+Fragment+Recall）

#### 高级便捷方法

| 方法 | 签名 | 说明 |
|------|------|------|
| `remember` | `(workspace_id, key, value, ttl=None) -> bool` | 设置记忆变量 |
| `recall` | `(workspace_id, query, top_k=5, budget_tokens=None) -> List[Dict]` | 召回记忆（变量+片段），结果带 `type` 字段 |
| `forget` | `(workspace_id, key) -> bool` | 删除变量 |
| `search` | `(workspace_id, query, top_k=5, threshold=0.3) -> Dict` | 混合搜索 |
| `get_context` | `(workspace_id, session_id=None) -> Dict` | 组装工作区上下文 |
| `remember_fragment` | `(workspace_id, content, fragment_type="info", ttl=None, importance_score=0.5) -> int` | 创建带 embedding 的片段 |
| `build_context` | `(workspace_id, session_id, user_query) -> str` | 构建注入上下文 |

> **关键约定**：`Engine.recall()` 必须返回 `List[Dict]` 且每项带 `type` 字段（`'variable'` 或 `'fragment'`），以保持一致的数据结构。

---

### 5.2 12 个业务模块

| 模块文件 | 主类 | 职责 |
|----------|------|------|
| [variables.py](file:///Users/howdy/pm/agent-memory-system/packages/core/src/agent_memory_core/modules/variables.py) | `VariableManager` | KV 变量 CRUD + session 作用域 + TTL + 文本抽取/模板渲染 |
| [fragments.py](file:///Users/howdy/pm/agent-memory-system/packages/core/src/agent_memory_core/modules/fragments.py) | `FragmentManager` | 片段 CRUD + 向量嵌入 + 语义搜索 + TTL 清理 + 向量一致性修复 + Prompt 模板 |
| [tables.py](file:///Users/howdy/pm/agent-memory-system/packages/core/src/agent_memory_core/modules/tables.py) | `TableManager` | 动态表 CRUD + 记录操作 + 批量 + NL 解析 |
| [graph.py](file:///Users/howdy/pm/agent-memory-system/packages/core/src/agent_memory_core/modules/graph.py) | `GraphManager` | 实体/关系管理 + BFS 图遍历 + LLM 抽取 + NL 查询 + 重复检测/合并 |
| [lifecycle.py](file:///Users/howdy/pm/agent-memory-system/packages/core/src/agent_memory_core/modules/lifecycle.py) | `LifecycleManager` | 半衰期衰减 + 冷标记 + 软删除/恢复 + 自动归档 + 重复检测/合并/冲突 |
| [recall.py](file:///Users/howdy/pm/agent-memory-system/packages/core/src/agent_memory_core/modules/recall.py) | `RecallManager` | 统一语义召回 + 预算控制 + 生命周期更新 + Profile 召回 |
| [search.py](file:///Users/howdy/pm/agent-memory-system/packages/core/src/agent_memory_core/modules/search.py) | `HybridSearchManager` | 4 信号融合搜索（语义+BM25+实体+时间）+ LLM 重排 + 权重可调 |
| [extraction.py](file:///Users/howdy/pm/agent-memory-system/packages/core/src/agent_memory_core/modules/extraction.py) | `ExtractionManager` | LLM 驱动记忆抽取（变量/事实/偏好/计划）+ 去重 + 启发式回退 |
| [compression.py](file:///Users/howdy/pm/agent-memory-system/packages/core/src/agent_memory_core/modules/compression.py) | `ContextCompressor` | 上下文压缩 + 三层记忆注入（Profile→语义→实体）+ token 预算 |
| [llm_backend.py](file:///Users/howdy/pm/agent-memory-system/packages/core/src/agent_memory_core/modules/llm_backend.py) | `LLMBackend`(ABC) | LLM 抽象层：`OpenAIBackend`、`ClaudeBackend`、`LocalModelBackend` + 工厂 |
| [observability.py](file:///Users/howdy/pm/agent-memory-system/packages/core/src/agent_memory_core/modules/observability.py) | `ObservabilityManager` | Trace 事件 + Dashboard 统计 + 质量评估（LLM/启发式） |
| [security.py](file:///Users/howdy/pm/agent-memory-system/packages/core/src/agent_memory_core/modules/security.py) | `SecurityManager` | SQL 安全（白/黑名单+模式匹配）+ SQL 注入检测 + XSS 检测 + 限流 + CSRF |

#### 关键算法

**记忆价值评分（MemoryValueScorer）**：
```
score = 0.30 * importance + 0.30 * relevance + 0.15 * decay + 0.15 * recency + 0.10 * completeness
```

**预算控制选择（贪心算法）**：
- 按价值密度（value_per_token）降序排列
- 贪心选择直到 token 预算耗尽

**三层记忆注入（ContextCompressor）**：
- Level 1 Profile：从 KV 变量召回用户画像（预算 600 tokens）
- Level 2 语义记忆：从 Fragment 语义召回（剩余预算）
- Level 3 实体扩展：从知识图谱扩展（独立预算）

---

### 5.3 存储抽象层（store/）

**文件**：[store/](file:///Users/howdy/pm/agent-memory-system/packages/core/src/agent_memory_core/store/)

三个 ABC 接口，所有方法以 `workspace_id` 作为隔离边界：

| 接口 | 实现 | 职责 |
|------|------|------|
| `RelationalStore` | `SQLiteStore`、PostgreSQLStore、MySQLStore | 关系数据库：变量/片段/表/图/生命周期/观测/FTS |
| `VectorStore` | `ChromaStore`、`MilvusStore`、`NullVectorStore` | 向量相似度搜索：add/search/get/update/delete |
| `CacheStore` | `RedisCacheStore`、`FakeRedisCacheStore`、`NullCacheStore` | 热数据缓存：set/get/delete/exists/expire/hash |

**工厂函数**（[factory.py](file:///Users/howdy/pm/agent-memory-system/packages/core/src/agent_memory_core/store/factory.py)）：
- `create_relational_store(config)`：根据 `database_url` 创建
- `create_vector_store(config)`：根据 `vector_backend` 创建
- `create_cache_store(config)`：根据 `cache_backend` 创建

**SQLiteStore 特性**：
- 原生 sqlite3 实现，无 ORM
- 线程本地连接（`threading.local()`），WAL 模式
- `ensure_schema()` 创建全部 17 张表 + FTS5 虚拟表 + 同步触发器
- 动态表物理表名：`dt_{workspace_id}_{table_name}`

---

### 5.4 配置与事件

#### CoreConfig

**文件**：[config.py](file:///Users/howdy/pm/agent-memory-system/packages/core/src/agent_memory_core/config.py)

基于 `pydantic-settings`，环境变量前缀 `AGENT_MEMORY_`，自动加载 `.env`。关键分组：
- 数据库：`database_url`、`database_echo`
- 向量：`vector_backend`（chroma/milvus/none）、`chroma_path`
- 缓存：`cache_backend`（redis/fakeredis/none）
- LLM：`llm_provider`（openai/deepseek/custom）、`llm_model`
- 记忆默认值：`context_max_tokens`（4000）、`recall_top_k`（5）

#### 事件系统

**文件**：[events.py](file:///Users/howdy/pm/agent-memory-system/packages/core/src/agent_memory_core/events.py)

核心层同步事件钩子，纯 Python 回调，无 async 依赖。Server 层用 EventBus 包装实现异步 + Webhook。

**17 种事件类型**（`MemoryEventType`）：
- 变量：`VARIABLE_SET`、`VARIABLE_DELETED`
- 片段：`FRAGMENT_CREATED/UPDATED/DELETED/EXPIRED`
- 表：`TABLE_CREATED/DELETED`、`RECORD_ADDED/UPDATED/DELETED`
- 图：`ENTITY_CREATED/UPDATED/DELETED`、`RELATIONSHIP_CREATED`、`RELATIONSHIP_DEACTIVATED`
- 生命周期：`MEMORY_COLD`、`MEMORY_RESTORED`、`MEMORY_SOFT_DELETED`
- 召回/搜索/抽取/上下文/维护：`RECALL_TRIGGERED/COMPLETED`、`SEARCH_TRIGGERED/COMPLETED`、`EXTRACTION_TRIGGERED/COMPLETED`、`CONTEXT_COMPRESSED`、`LIFECYCLE_MAINTENANCE`

**EventEmitter**：`on(event_type, handler)` / `off()` / `emit(event)`，错误被捕获不传播。

---

## 6. SDK 生态

### 6.1 Python SDK

**包名**：`agent-memory-sdk`，版本 0.1.0，Python >= 3.10。

**文件**：[sdk-python/](file:///Users/howdy/pm/agent-memory-system/sdk-python/)

#### 双模式客户端

| 模式 | 客户端 | 传输层 | 用途 |
|------|--------|--------|------|
| HTTP | `MemoryClient`（同步） | `HttpTransport`（httpx） | 连接远程后端 |
| HTTP | `AsyncMemoryClient`（异步） | `_AsyncHttpTransport`（httpx.AsyncClient） | 异步场景 |
| Embedded | `MemoryClient(mode="embedded")` | `EmbeddedTransport` | 直连本地 service 层，跳过 HTTP |

#### 客户端结构

```python
class MemoryClient:
    # 7 个 API 子模块
    variables: VariablesAPI      # KV 变量 CRUD + batch + TTL
    fragments: FragmentsAPI      # 片段 CRUD + 语义搜索 + batch_delete
    tables: TablesAPI            # 表 CRUD + 记录 + batch + 过滤查询
    graph: GraphAPI              # 实体/关系 + 邻居 + NL 查询 + 合并
    recall: RecallAPI            # 自动召回 + 配置 + 统计 + summary/inject
    events: EventsAPI            # 事件列表 + SSE 流订阅
    webhooks: WebhooksAPI        # Webhook CRUD + 测试 + 投递记录

    # 高层便捷方法
    remember(key, value, ttl=None) -> bool
    recall_context(query, top_k=5) -> str
    forget(key) -> bool
    search(query, top_k=5, threshold=0.3) -> List[Dict]
```

#### 传输层

**`Transport`（ABC）** → `HttpTransport` / `EmbeddedTransport`

- `HttpTransport`：httpx.Client，自动注入 `Authorization` + `X-Workspace-Id` 头，支持 SSE 流式
- `EmbeddedTransport`：直连 `app.services` 函数，巨大的路径映射表路由到对应 service，不支持流式

#### 异常层级

```
AgentMemoryError
├── TransportError
│   └── HTTPError (status_code, detail)
│       ├── AuthenticationError (401)
│       ├── PermissionDeniedError (403)
│       └── NotFoundError (404)
├── ValidationError
└── EmbeddedModeError
```

#### 集成

| 集成文件 | 用途 |
|----------|------|
| [langchain.py](file:///Users/howdy/pm/agent-memory-system/sdk-python/src/agent_memory/integrations/langchain.py) | 返回 13 个 LangChain `StructuredTool`（7 记忆 + 6 图谱） |
| [langchain_memory.py](file:///Users/howdy/pm/agent-memory-system/sdk-python/src/agent_memory/integrations/langchain_memory.py) | `AgentMemoryLangChain` 兼容 LangChain Agent 的 memory 参数 |
| [mcp.py](file:///Users/howdy/pm/agent-memory-system/sdk-python/src/agent_memory/integrations/mcp.py) | 独立 MCP Server（7 个记忆工具，stdio 传输） |

---

### 6.2 TypeScript SDK

**包名**：`@agent-memory/sdk`，版本 0.1.0，ESM，**零运行时依赖**。

**文件**：[sdk-typescript/](file:///Users/howdy/pm/agent-memory-system/sdk-typescript/)

#### 客户端结构

```typescript
class MemoryClient {
  // 9 个 API 子模块（比 Python 多 WorkspaceAPI + ApiKeyAPI）
  variables: VariablesAPI
  fragments: FragmentsAPI
  tables: TablesAPI
  graph: GraphAPI
  recall: RecallAPI
  workspaces: WorkspaceAPI     // Python SDK 无此模块
  apiKeys: ApiKeyAPI           // Python SDK 无此模块
  events: EventsAPI
  webhooks: WebhooksAPI

  // 高层便捷方法（全异步）
  remember(key, value, ttl?): Promise<boolean>
  recallContext(query, topK?): Promise<string>
  forget(key): Promise<boolean>
  search(query, topK?): Promise<any[]>
}
```

#### 传输层

`Transport` 类（非抽象，直接实例化）：
- 基于原生 `fetch` + `AbortController` 实现超时
- `requestStream()` 返回 `AsyncIterable<string>`（SSE 流式）
- 自动注入 `Authorization` + `X-Workspace-Id` 头

---

## 7. 前端架构（frontend/）

**文件**：[frontend/](file:///Users/howdy/pm/agent-memory-system/frontend/)

### 技术栈

React 19 + TypeScript 6 + Vite 8 + Ant Design 6 + Zustand 5 + TanStack React Query 5

### Provider 嵌套（App.tsx）

```
QueryClientProvider（React Query）
  └─ ConfigProvider（antd，locale zhCN，主题色 #667eea）
     └─ AntApp（antd App 容器）
        └─ ErrorBoundary（全局错误捕获）
           └─ BrowserRouter
              └─ Suspense（路由懒加载）
                 └─ AuthGuard + AppLayout（受保护路由）
```

### 状态管理分层

| 层级 | 工具 | 职责 |
|------|------|------|
| 服务端状态 | React Query | 缓存、轮询、失效、乐观更新（`useMemoryQueries` 封装 50+ hooks） |
| 客户端全局状态 | Zustand | `authStore`（认证）、`workspaceStore`（工作空间）、`appStore`（加载态），localStorage 持久化 |
| 组件局部状态 | React useState | 表单、选中项、分页等 |

### 路由（16 条受保护路由）

| 路径 | 页面 | 用途 |
|------|------|------|
| `/login` | Login | 登录/注册（公开） |
| `/` | Dashboard | 仪表盘，聚合统计概览 |
| `/agent-chat` | AgentChat | Agent 对话（虚拟列表 + SSE 流式 + 三栏布局） |
| `/agent-tools` | AgentTools | Agent 工具管理 |
| `/variables` | Variables | 记忆变量管理 |
| `/extraction` | Extraction | 记忆抽取 |
| `/tables` | Tables | 记忆表管理 + NL2SQL |
| `/fragments` | Fragments | 记忆片段 + 语义搜索 |
| `/recall` | Recall | 自动召回 |
| `/long-term` | LongTerm | 长期记忆版本管理 |
| `/observability` | Observability | 观测中心（recharts 图表） |
| `/lifecycle` | Lifecycle | 生命周期管理 |
| `/graph-memory` | GraphMemory | 知识图谱可视化 |
| `/hybrid-search` | HybridSearch | 混合搜索权重调节 |
| `/system` | System | 系统集成（LLM/插件/性能/安全） |
| `/workspace/settings` | WorkspaceSettings | Workspace + API Key 管理 |

### API 客户端（api.ts）

- Axios 实例，`baseURL: '/api/v1'`，30s 超时
- 请求拦截器自动注入 `Authorization: Bearer <token>` + `X-Workspace-Id`
- 响应拦截器：401 清凭证跳转 `/login`，>=400 显示 antd 通知
- SSE 端点使用原生 `fetch` 直连后端（绕过 Vite proxy，因 http-proxy 不支持真流式）
- 集成 `@agent-memory/sdk` 的 `MemoryClient`

### 关键组件

| 组件 | 用途 |
|------|------|
| `AppLayout` | 全局布局（侧栏菜单 + 顶栏 + Outlet） |
| `AuthGuard` | 路由守卫（checkAuth 验证 token） |
| `GraphVisualizer` | 知识图谱可视化（vis-network，>200 实体走 Web Worker，>500 实体聚类） |
| `SessionSidebar` | 会话列表（无限滚动 + 搜索 + 多选 + 右键菜单） |
| `SummaryPanel` | 会话摘要面板（质量评分 + 编辑 + 历史版本） |
| `AdvancedFilter` | 通用高级过滤（8 操作符 + AND/OR + 预设持久化） |
| `MarkdownRenderer` | Markdown 渲染（代码块复制 + 流式光标） |
| `SchemaForm` | JSON Schema 自动生成 antd 表单 |

### 性能优化

- 路由级代码分割（`lazy` + `Suspense`）
- Vite 手动分包（vendor-react/antd/recharts/markdown）
- 大图谱计算走 Web Worker（200 实体阈值）
- 超大图谱聚类展示（500 实体阈值）
- 虚拟列表（`useVirtualizer`）用于长消息列表
- React Query `staleTime` 避免重复请求，关键指标 30s 轮询
- 无限滚动分页（会话列表）
- PWA 离线支持（vite-plugin-pwa）

---

## 8. 数据模型与存储设计

### 三类记忆数据模型

| 类型 | 模型 | 存储 | 特点 |
|------|------|------|------|
| 变量 | `MemoryVariable` | SQLite/PG + Redis 缓存 | 轻量 KV，支持 session 作用域 + TTL |
| 片段 | `MemoryFragment` | SQLite/PG + ChromaDB 向量 | 语义记忆，带 embedding + 重要性 + 生命周期 |
| 表 | `MemoryTable` | SQLite/PG 元数据 + 动态物理表 | 结构化数据，动态 Schema |

### 存储后端矩阵

| 存储类型 | 开发环境 | 生产环境 | 抽象接口 |
|----------|----------|----------|----------|
| 关系数据库 | SQLite（WAL 模式） | PostgreSQL 16 | `RelationalStore` |
| 向量数据库 | ChromaDB（本地持久化） | ChromaDB（容器化）/ Milvus | `VectorStore` |
| 缓存 | FakeRedis | Redis 7 | `CacheStore` |
| 事件总线 | InMemoryEventBus | RedisEventBus | `EventBus` |
| 全文搜索 | SQLite FTS5 | PostgreSQL ILIKE 兜底 | `fts_search()` |

### Outbox Pattern（跨存储原子性）

为保证 SQLite（片段元数据）与 ChromaDB（向量）的最终一致性：

1. 创建片段时：先写 SQLite + 写 `vector_outbox` 表（同一事务）
2. Outbox 调度器（每 30s）扫描 `vector_outbox`，将待同步条目写入 ChromaDB
3. 成功后标记 outbox 条目完成；失败重试（最多 5 次，指数退避）

### 双时间线模型（知识图谱）

`GraphRelationship` 表包含双时间线：
- **有效时间线**（valid_from / valid_to）：关系在现实世界中的有效区间
- **记录时间线**（observed_at / expired_at）：关系在系统中的记录/失效时间

支持时序查询：查询某时间点的关系状态、关系变更历史。

---

## 9. 依赖关系

### 后端依赖（pyproject.toml）

| 类别 | 依赖 | 用途 |
|------|------|------|
| Web 框架 | fastapi, uvicorn[standard] | 异步 Web 框架 + ASGI 服务器 |
| ORM/迁移 | sqlalchemy, alembic | ORM + 数据库迁移 |
| 数据库驱动 | psycopg2-binary | PostgreSQL 驱动 |
| 向量数据库 | chromadb, pymilvus | 向量存储 |
| 缓存 | redis, fakeredis | 缓存 + FakeRedis（测试） |
| LLM | openai, langchain, llama-index | LLM 调用 + Agent 框架 |
| 认证 | python-jose, passlib, bcrypt, pyjwt, cryptography | JWT + 密码哈希 + 加密 |
| 基础设施 | pydantic-settings, structlog, prometheus-client, prometheus-fastapi-instrumentator, httpx | 配置/日志/指标/HTTP |
| MCP | mcp>=1.0.0 | MCP Server |
| 本地包 | agent-memory-sdk | Python SDK |

### 核心包依赖（packages/core/pyproject.toml）

极简：仅 `pydantic` + `pydantic-settings`。可选 extras：
- `sqlite`（原生支持）、`postgresql`（psycopg2-binary）、`mysql`（mysqlclient）
- `chroma`（chromadb）、`milvus`（pymilvus）、`redis`（redis + fakeredis）
- `llm`（openai + langchain）、`all`（合集）

### Python SDK 依赖

- 核心：`httpx` + `pydantic`
- `embedded` extra：`sqlalchemy` + `chromadb` + `redis`
- `langchain` extra：`langchain-core`
- `mcp` extra：`mcp`

### TypeScript SDK 依赖

**零运行时依赖**，仅用原生 `fetch`/`AbortController`/`URLSearchParams`/`TextDecoder`。devDependencies 仅 `typescript`。

### 前端依赖

| 类别 | 依赖 |
|------|------|
| 核心 | react, react-dom, react-router-dom |
| UI | antd |
| 状态 | zustand, @tanstack/react-query, @tanstack/react-virtual |
| HTTP | axios, @agent-memory/sdk（本地） |
| 可视化 | vis-network, vis-data, recharts |
| Markdown | react-markdown, remark-gfm |
| PWA | vite-plugin-pwa |
| 构建 | vite, @vitejs/plugin-react, typescript |
| 测试 | vitest, @testing-library/react, jsdom, @playwright/test |

---

## 10. 项目运行方式

### 方式一：Docker Compose（推荐）

#### 开发环境（SQLite + FakeRedis）

```bash
cd agent-memory-system
docker-compose up -d
# 后端: http://localhost:8000
# 前端(可选): docker-compose --profile full up -d → http://localhost:3000
```

#### 生产环境（PostgreSQL + Redis + ChromaDB + 可观测栈）

```bash
docker-compose -f docker-compose.prod.yml up -d
# 后端: http://localhost:8000
# Jaeger UI: http://localhost:16686
# Prometheus: http://localhost:9090
# Grafana: http://localhost:3001 (admin/admin)
# ChromaDB: http://localhost:8001
```

### 方式二：手动启动

#### 后端

```bash
cd agent-memory-system/backend
pip install -r requirements.txt

# 先安装本地 SDK 依赖
pip install -e ../sdk-python/

# 初始化数据库
python -c "from app.core.init_db import init_database; init_database()"

# 启动服务
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

#### 前端

```bash
cd agent-memory-system/frontend
npm install
npm run dev
# http://localhost:5173
```

### 方式三：Kubernetes

```bash
kubectl apply -f k8s/deployment.yaml
```

### 方式四：核心包嵌入式使用

```python
from agent_memory_core import MemoryEngine

engine = MemoryEngine.from_config()
engine.remember(workspace_id=1, key="user_name", value="鑫海")
results = engine.recall(workspace_id=1, query="用户叫什么")
```

### 方式五：SDK 使用

#### Python SDK

```python
from agent_memory import MemoryClient

# HTTP 模式
client = MemoryClient(base_url="http://localhost:8000/api/v1", token="your-jwt-token")
client.remember("user_name", "鑫海")
context = client.recall_context("用户叫什么")

# Embedded 模式（直连本地 service，无需 HTTP）
client = MemoryClient(mode="embedded", db_path="agent_memory.db")
```

#### TypeScript SDK

```typescript
import { MemoryClient } from '@agent-memory/sdk';

const client = new MemoryClient({
  baseUrl: 'http://localhost:8000/api/v1',
  token: 'your-jwt-token',
});
await client.remember('user_name', '鑫海');
const context = await client.recallContext('用户叫什么');
```

### 环境变量配置

创建 `.env` 文件（后端根目录）：

```env
# 数据库
DATABASE_URL=sqlite:///agent_memory.db
# 生产: DATABASE_URL=postgresql://memory_user:password@localhost:5432/agent_memory

# Redis
REDIS_URL=redis://localhost:6379/0

# 向量数据库
VECTOR_BACKEND=chroma
CHROMA_PERSIST_DIR=./chromadb_data

# 认证
JWT_SECRET_KEY=your-secret-key-change-in-production
JWT_EXPIRATION_HOURS=24

# LLM
DEEPSEEK_API_KEY=your-api-key
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat

# 事件总线
EVENT_BUS_BACKEND=memory  # 生产: redis

# 监控
ENABLE_METRICS=true
ENABLE_TRACING=false
# 生产: ENABLE_TRACING=true, OTLP_ENDPOINT=http://otel-collector:4317

# 日志
LOG_LEVEL=INFO
LOG_JSON=false  # 生产: true

# MCP Server
MCP_ENABLED=true
MCP_TRANSPORT=stdio
```

---

## 11. 测试体系

### 后端测试

**配置**：[pytest.ini](file:///Users/howdy/pm/agent-memory-system/backend/pytest.ini)

```bash
cd backend

# 运行所有测试
pytest tests/ -v

# 仅单元测试
pytest tests/ -v -m unit

# 仅集成测试
pytest tests/ -v -m integration

# 带覆盖率报告
pytest tests/ --cov=app --cov-report=html

# 排除性能测试（默认已排除）
pytest tests/ -m "not performance"
```

**测试标记**：`unit`、`integration`、`e2e`、`performance`（默认排除）、`slow`、`asyncio`

**关键测试文件**：

| 测试文件 | 覆盖范围 |
|----------|----------|
| `test_services.py` | 核心服务单元测试 |
| `test_integration.py` | 端到端集成测试 |
| `test_mcp_server.py` | MCP Server（40 个测试） |
| `test_advanced_recall.py` | P0/P1 召回优化（41 个测试） |
| `test_smart_forgetting.py` | 智能遗忘机制 |
| `test_sql_safety.py` | SQL 安全检查 |
| `test_longmemeval_benchmark.py` | LongMemEval 基准测试 |

**测试数据隔离**：`conftest.py` 中 `_cleanup_test_data_autouse` fixture 自动清理 9 张表 + 动态物理表（`memory_999_*` 模式）。

### 核心包测试

```bash
cd packages/core
pytest tests/ -v
```

### SDK 测试

```bash
# Python SDK
cd sdk-python
pytest tests/ -v

# TypeScript SDK
cd sdk-typescript
npm test
```

### 前端测试

```bash
cd frontend

# 单元测试（Vitest）
npm test
npm run test:watch

# E2E 测试（Playwright）
npm run test:e2e
```

### 基准测试

```bash
cd backend

# 合成数据集（无需 LLM）
python -m app.benchmarks.runner --sample

# 真实 LongMemEval 数据集
python -m app.benchmarks.runner --data path/to/longmemeval_s.json --limit 50
```

---

## 12. 部署与运维

### Docker 多阶段构建

**文件**：[Dockerfile](file:///Users/howdy/pm/agent-memory-system/Dockerfile)

1. **Builder 阶段**：安装 SDK + 后端依赖到 `/install`
2. **Production 阶段**：仅复制安装包 + 应用代码，创建非 root 用户 `appuser`
3. 健康检查使用 `/api/v1/health/live`（轻量级，无依赖检查）
4. 默认 4 个 Uvicorn worker（`UVICORN_WORKERS=4`）

### 可观测性栈

| 组件 | 端口 | 用途 |
|------|------|------|
| Prometheus | 9090 | 指标采集 |
| Grafana | 3001 | 指标可视化（admin/admin） |
| Jaeger | 16686 | 链路追踪 UI |
| OTel Collector | 4317/4318 | 追踪数据收集 |

**Grafana 仪表盘**：[deploy/grafana/dashboards/memory-system-overview.json](file:///Users/howdy/pm/agent-memory-system/deploy/grafana/dashboards/memory-system-overview.json)

### Prometheus 指标

关键业务指标：
- `memory_operations_total[operation,memory_type]`（Counter）
- `memory_recall_latency_seconds`（Histogram）
- `memory_recall_hit_rate`（Gauge）
- `llm_tokens_used_total[operation,token_type]`（Counter）
- `webhook_deliveries_total[status]`（Counter）
- `event_bus_published_total[event_type]`（Counter）
- `active_memories_gauge[memory_type]`（Gauge）

### API 版本管理

- 当前版本：`1.0.0`
- 稳定性标记：`STABLE`（auth/memory/agent/workspaces）、`BETA`（webhooks/events/graph/hybrid）
- DEPRECATED 端点会注入 `Deprecation: true` + `Sunset` 响应头

### 安全特性

- JWT Token 认证（HS256，默认 24h 过期）
- PBKDF2 密码哈希（200000 次迭代）
- 多租户数据隔离（workspace_id）
- SQL 注入检测（白名单 + 黑名单 + 模式匹配 + EXPLAIN 预检）
- XSS 防护和 HTML 清理
- CSRF Token 保护
- 速率限制（默认 100 次/分钟/端点）
- RBAC 权限框架（owner/admin/member/viewer 四级角色）
- 断路器模式（LLM 后端熔断保护）
- 安全事件日志和审计追踪

---

## 附录：关键设计约定

以下约定来自项目积累的工程经验（project_memory）：

1. **静态路由优先**：静态路由（如 `/batch`、`/search`）必须注册在动态路由（如 `/{id}`）之前
2. **统一响应信封**：`{success, data, error, trace_id}`
3. **lifecycle_status 是唯一真相源**：`memory_fragments.lifecycle_status` 是记忆状态的 Single Source of Truth，`memory_lifecycle` 表仅用于审计日志
4. **TableManager.create() 必须检查返回值**：检查 `RelationalStore.create_table()` 返回值，失败抛 `ValueError` 防止孤立物理表
5. **空路径字符串**：API 路由使用 `@router.post("")` 而非 `@router.post("/")` 避免 307 重定向
6. **top_k 参数**：`AutoRecallRequest` 必须包含 `top_k: Optional[int]` 字段
7. **recall 返回结构**：`Engine.remember()` 必须返回 `List[Dict]` 带 `type` 字段（`'variable'` 或 `'fragment'`）
8. **HTTP 201**：资源创建端点返回 201 而非 200
9. **HTTPException**：错误响应使用 `HTTPException` 而非返回 200 + `success: False`
10. **响应字段过滤**：`AutoRecallService` 响应必须排除内部配置字段（`semantic_top_k`、`similarity_threshold`、`hybrid_search_alpha`）
11. **Pydantic 校验前置**：使用 `Field` 和 `Literal` 约束做输入校验，而非手动 if 检查
12. **Outbox Pattern**：跨存储操作（SQLite + ChromaDB）必须使用 Outbox Pattern 保证原子性
13. **SQL 安全检查**：`_strip_string_literals` 方法移除字符串字面量后再检查禁止模式，避免字符串内分号误报
