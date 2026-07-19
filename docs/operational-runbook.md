# 故障恢复运维手册 (Operational Runbook)

> Agent Memory System 后端生产运维手册。用于值班工程师快速定位与恢复常见故障。

## 1. 系统概览

| 组件 | 作用 | 故障影响 |
|------|------|----------|
| FastAPI 应用 | HTTP API 服务 | 全站不可用 |
| 关系型数据库 (SQLite/Postgres) | 记忆变量/表/片段元数据 | 读写失败 |
| ChromaDB | 向量检索 | 语义召回降级 |
| Redis | 事件总线 (可选) / 缓存 | 事件分发降级为内存模式 |
| OTel Collector | 链路追踪 (可选) | 仅影响可观测性，不影响业务 |
| Webhook Worker | 异步投递重试 | 事件通知延迟 |

## 2. 健康检查

```bash
# 存活探针
curl -fsS http://<host>:8000/health

# 部署前预检（详见 deployment-checklist.md）
python scripts/pre_deploy_check.py
```

关键响应头（每个响应均携带，便于排障）：
- `X-Request-ID`：请求唯一 ID，日志关联用
- `API-Version` / `API-Stability`：当前 API 版本与端点稳定性

## 3. 统一错误响应格式

所有错误均为统一结构，排障时以 `trace_id` 关联日志：

```json
{
  "code": "NOT_FOUND",
  "message": "Resource not found",
  "details": null,
  "trace_id": "8f1c...032hex 或 uuid",
  "timestamp": "2026-07-18T12:00:00+00:00"
}
```

| code | HTTP | 含义 |
|------|------|------|
| AUTH_INVALID_CREDENTIALS | 401 | 认证失败 |
| FORBIDDEN | 403 | 无权访问 |
| NOT_FOUND | 404 | 资源不存在 |
| CONFLICT | 409 | 资源冲突 |
| VALIDATION_ERROR | 400 | 业务校验失败 |
| WEBHOOK_DELIVERY_FAILED | 502 | Webhook 投递失败 |
| INTERNAL_ERROR | 500 | 未预期错误 |

## 4. 常见故障处置

### 4.1 API 全站 5xx / 无响应

1. 确认进程存活：`ps aux | grep uvicorn` 或容器状态
2. 查看最近日志中的 `INTERNAL_ERROR` 与对应 `trace_id`
3. 检查数据库连通性：`python scripts/pre_deploy_check.py --json`
4. 若为 OOM/句柄泄漏，滚动重启实例；观察是否复发

**回滚**：`kubectl rollout undo deployment/<name>` 或重新部署上一个镜像 tag。

### 4.2 数据库连接失败

- 症状：`database` 预检 FAIL；接口大面积 500
- 处置：
  1. 校验 `DATABASE_URL` 是否正确、凭据是否过期
  2. Postgres：检查连接数是否打满（`SELECT count(*) FROM pg_stat_activity;`）
  3. SQLite：检查磁盘空间与文件锁（`database is locked` → 降低并发写）

### 4.3 向量检索异常 (ChromaDB)

- 症状：语义召回返回空/报错；`chromadb` 预检 FAIL
- 处置：
  1. 检查 `CHROMA_PERSIST_DIR` 磁盘与权限
  2. 数据损坏时，客户端会自动清空目录并重建（见 `get_chromadb_client`）；确认重建日志
  3. 关系库中的记忆元数据不受影响，可先降级为关键词检索

### 4.4 事件不分发 / Webhook 未触发

- 症状：订阅方收不到事件
- 处置：
  1. 确认 `EVENT_BUS_BACKEND`（`memory` 为单进程内存总线，多副本间不共享）
  2. 多副本部署必须使用 `redis`；检查 Redis 连通性（预检 `redis` 项）
  3. Webhook 投递失败会按指数退避重试（1m/5m/30m/2h/8h/24h，共 6 次）
  4. 查询投递记录：`webhook_deliveries` 表 `success=0` 且 `attempt` 递增

### 4.5 Webhook 重试风暴 / 下游过载

- 临时停用某 webhook：`update_webhook(id, active=False)`
- 停止后台重试 worker：应用内 `stop_webhook_worker()`（或重启且不触发）

### 4.6 链路追踪缺失

- 症状：Jaeger/Tempo 无 span
- 处置：
  1. 确认 `ENABLE_TRACING=true` 且 `OTLP_ENDPOINT` 正确
  2. 预检 `otel` 项做 TCP 连通性判断
  3. Tracing 故障**不影响业务**，可暂时关闭 `ENABLE_TRACING` 止血

## 5. 关键日志与指标

- 结构化日志：生产设 `LOG_JSON=true`，按 `request_id` / `trace_id` 检索
- Prometheus 指标（`/metrics`）：
  - `webhook_deliveries_total{status}`：投递成功/失败计数
  - `webhook_delivery_latency_seconds`：投递延迟
  - `event_bus_published_total{event_type}`：事件发布计数

## 6. 升级与回滚

1. 部署前执行 `scripts/pre_deploy_check.py`
2. 数据库迁移：`alembic upgrade head`（回滚 `alembic downgrade -1`）
3. 灰度：先滚动 1 个副本，观察错误率与 p99 延迟
4. 回滚触发条件：5xx 比例 > 1% 持续 5 分钟，或核心接口 p99 明显劣化

## 7. 升级 API 版本 / 弃用端点

- 版本与稳定性定义见 `app/core/versioning.py`
- 弃用端点会返回 `Deprecation` + `Sunset` 响应头；通知调用方在 Sunset 前迁移
- 变更记录见 `CHANGELOG.md`

## 8. 联系与升级路径

- L1 值班：按本手册处置常见故障
- L2 后端 Owner：数据损坏、迁移失败、需回滚
- L3 架构：涉及数据模型或跨系统的重大故障
