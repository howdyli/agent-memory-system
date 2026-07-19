# Changelog

All notable changes to the Agent Memory System backend API are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.0.0] - 2026-07-18

### Added
- **统一错误处理**：新增 `app/core/errors.py`，提供机器可读的 `ErrorCode` 枚举、
  标准化 `ErrorResponse` 响应体（`code` / `message` / `details` / `trace_id` / `timestamp`）
  及 `AppException` 异常体系（`NotFoundError` / `ConflictError` / `ForbiddenError` /
  `ValidationError` / `AuthError`）。
- **全局异常处理器**：所有 `AppException`、`HTTPException`、请求校验错误与未捕获异常
  统一转换为 `ErrorResponse` 格式。
- **Request ID 链路**：新增 Request ID 中间件，为每个请求生成/透传 `X-Request-ID`，
  绑定 structlog 上下文，并写入响应头，便于日志与链路关联。
- **API 版本管理**：新增 `app/core/versioning.py`，每个响应返回 `API-Version` 与
  `API-Stability` 头；`DEPRECATED` 端点附加 `Deprecation` / `Sunset` 头。
- **测试覆盖率量化**：集成 `pytest-cov`，第一阶段覆盖率门槛 `fail_under = 60`。
- **生产部署核查**：新增部署预检脚本 `scripts/pre_deploy_check.py`、性能基准测试
  `tests/test_performance_baselines.py`、运维手册 `docs/operational-runbook.md`
  与部署检查清单 `docs/deployment-checklist.md`。

### Changed
- 迁移各 API 路由（webhooks / memory_variables / memory_tables / memory_fragments /
  auth / workspace）由裸 `HTTPException` 改为统一 `AppException` 体系。
- 错误响应结构由 `{"detail": ...}` 统一为 `ErrorResponse`；原 `detail` 文案映射到
  `message` 字段。HTTP 状态码保持向后兼容。

### Endpoint Stability
- **Stable**：`/api/v1/auth`、`/api/v1/memory`、`/api/v1/agent`、`/api/v1/workspaces`
- **Beta**：`/api/v1/webhooks`、`/api/v1/events`、图谱记忆与混合检索端点
- **Deprecated**：无

[Unreleased]: https://example.com/compare/v1.0.0...HEAD
[1.0.0]: https://example.com/releases/tag/v1.0.0
