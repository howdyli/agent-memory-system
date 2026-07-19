# 部署检查清单 (Deployment Checklist)

> 每次生产部署前逐项确认。可配合自动化预检脚本 `backend/scripts/pre_deploy_check.py` 使用。

## 0. 自动化预检（先跑这个）

```bash
cd backend
python scripts/pre_deploy_check.py          # 存在 FAIL 则退出码 1
python scripts/pre_deploy_check.py --json    # 机器可读，供 CI/CD 消费
python scripts/pre_deploy_check.py --force    # 强制放行（风险自负，仅应急）
```

预检覆盖：环境变量/配置、数据库、Redis、ChromaDB、OTel 可达性、关键依赖。

## 1. 配置与密钥（Blocker）

- [ ] `JWT_SECRET_KEY` 已设置且 ≥ 32 字符（**未设置将阻断部署**）
- [ ] `ENCRYPTION_PASSWORD` / `ENCRYPTION_SALT` 已配置（启用敏感字段加密）
- [ ] `DATABASE_URL` 指向生产数据库（生产不建议 SQLite）
- [ ] `CORS_ORIGINS` 限定为可信来源，**不使用 `*`**
- [ ] `LOG_JSON=true`（结构化日志）
- [ ] `LOG_LEVEL` 设为 `INFO` 或 `WARNING`
- [ ] 密钥通过 Secret 管理（K8s Secret / Vault），**不写入镜像或仓库**

## 2. 数据存储

- [ ] 数据库可连通（预检 `database` 项为 OK）
- [ ] 数据库迁移已应用：`alembic upgrade head`
- [ ] 迁移在预发环境验证过，且有回滚方案（`alembic downgrade -1`）
- [ ] ChromaDB `CHROMA_PERSIST_DIR` 持久卷已挂载、有足够磁盘
- [ ] 备份策略就绪（关系库定时备份 + 向量库目录快照）

## 3. 事件总线与 Webhook

- [ ] 多副本部署时 `EVENT_BUS_BACKEND=redis`（`memory` 仅限单副本）
- [ ] Redis 可连通且**非 fakeredis 降级**（预检 `redis` 项为 OK）
- [ ] Webhook 重试 worker 随应用启动
- [ ] 已知外部 webhook 下游具备幂等消费能力（重试可能重复投递）

## 4. 可观测性

- [ ] `ENABLE_METRICS=true`，`/metrics` 可被 Prometheus 抓取
- [ ] 如启用 tracing：`ENABLE_TRACING=true` 且 `OTLP_ENDPOINT` 可达
- [ ] Grafana 面板与告警规则已就绪（见 `deploy/grafana`、`deploy/prometheus/alerts.yml`）

## 5. 质量门禁（CI 已覆盖）

- [ ] CI 全绿：lint (ruff) + type (mypy) + 单元/集成测试
- [ ] 覆盖率不低于 `.coveragerc` 的 `fail_under`（当前 48%，路线图 → 60% → 80%）
- [ ] 性能基准（nightly）通过：`pytest -m performance`
  - 记忆变量 CRUD p99 < 100ms
  - 事件发布 p99 < 10ms
  - 错误响应构造 p99 < 1ms

## 6. 应用与容器

- [ ] 镜像 tag 明确（不使用 `latest`），可追溯到 Git commit
- [ ] 健康探针配置正确：liveness/readiness 指向 `/health`
- [ ] 资源 requests/limits 已设置（CPU/内存），避免 OOMKill
- [ ] 优雅关闭：SIGTERM 时停止接收新请求并完成在途请求

## 7. 灰度与验证

- [ ] 先滚动 1 个副本（canary），观察 5–10 分钟
- [ ] 核对响应头 `API-Version` 与预期一致
- [ ] 抽样验证核心接口：认证、记忆变量读写、召回
- [ ] 错误率（5xx）与 p99 延迟无明显劣化后再全量

## 8. 发布后

- [ ] `CHANGELOG.md` 已更新本次变更
- [ ] 若有弃用端点：确认 `Deprecation` + `Sunset` 头正确，已通知调用方
- [ ] 监控面板确认无异常告警
- [ ] 回滚预案与责任人已明确（见 `operational-runbook.md`）

## 回滚判定

出现以下任一情况，按 runbook 回滚到上一稳定版本：
- 5xx 比例 > 1% 且持续 5 分钟
- 核心接口 p99 延迟显著劣化
- 数据库迁移导致数据不一致
