"""
Agent Memory System — 主入口

集成基础设施：
- Phase 0: 集中配置 / 结构化日志 / Prometheus 指标 / 健康检查
- Phase 4: EventBus + Webhook
- Phase 5: OpenTelemetry 链路追踪
"""
import contextvars
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import get_settings
from app.core.errors import (
    AppException,
    ErrorCode,
    ErrorResponse,
    get_trace_id,
)
from app.core.log_setup import get_logger, setup_logging
from app.core.versioning import (
    API_VERSION,
    EndpointStability,
    resolve_stability,
    resolve_sunset,
)
from app.api import (
    health, auth, memory_variables, memory_extraction, memory_tables,
    memory_fragments, auto_recall, long_term_memory, system_integration,
    agent, memory_lifecycle, graph_memory, hybrid_search,
    memory_observability, sessions, workspace, webhooks, events,
)


# ------------------------------------------------------------------
# 配置 & 日志（模块加载时即初始化，确保后续 import 能拿到 logger）
# ------------------------------------------------------------------
settings = get_settings()
setup_logging(log_level=settings.LOG_LEVEL, json_logs=settings.LOG_JSON)
logger = get_logger("app.main")

# 请求级 Request ID 上下文（供日志与异常处理器读取）
_request_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default=""
)


def _current_trace_id() -> str:
    """优先使用当前请求的 Request ID，其次降级到 OTel/UUID。"""
    return _request_id_ctx.get() or get_trace_id()


def _status_to_error_code(status_code: int) -> ErrorCode | str:
    """将 HTTP 状态码映射到稳定错误码（用于兼容旧的 HTTPException）。"""
    mapping = {
        400: ErrorCode.VALIDATION_ERROR,
        401: ErrorCode.AUTH_INVALID_CREDENTIALS,
        403: ErrorCode.FORBIDDEN,
        404: ErrorCode.NOT_FOUND,
        409: ErrorCode.CONFLICT,
        422: ErrorCode.VALIDATION_ERROR,
    }
    if status_code >= 500:
        return ErrorCode.INTERNAL_ERROR
    return mapping.get(status_code, f"HTTP_{status_code}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    from app.services.memory_lifecycle_service import (
        start_lifecycle_scheduler,
        stop_lifecycle_scheduler,
    )
    from app.services.memory_fragment_service import (
        start_outbox_scheduler,
        stop_outbox_scheduler,
    )
    from app.core.event_bus import get_event_bus
    from app.services.webhook_service import (
        dispatch_event_to_webhooks,
        start_webhook_worker,
        stop_webhook_worker,
    )
    from app.services.llm_retry_queue import (
        start_retry_worker,
        stop_retry_worker,
    )
    from app.core.tracing import init_tracing, shutdown_tracing

    # 初始化 OTel 链路追踪
    init_tracing(app)

    # 启动各子系统
    start_lifecycle_scheduler()
    start_outbox_scheduler()
    event_bus = get_event_bus()
    await event_bus.start()
    start_webhook_worker()
    # 将 EventBus 事件分发给匹配的活跃 Webhook（订阅全部事件类型，由 dispatch 内部按 event_types 过滤）
    webhook_subscription_id = await event_bus.subscribe(["*"], dispatch_event_to_webhooks)
    start_retry_worker()

    yield

    # 优雅关闭（逆序）
    shutdown_tracing()
    stop_retry_worker()
    await event_bus.unsubscribe(webhook_subscription_id)
    stop_webhook_worker()
    stop_outbox_scheduler()
    await event_bus.stop()
    stop_lifecycle_scheduler()


app = FastAPI(
    title="Agent Memory System",
    description="""
## Agent Memory System Backend API

智能记忆管理系统，提供以下核心能力：

- **记忆片段管理** — 语义记忆的 CRUD、批量操作、重要性评分
- **知识图谱** — 实体/关系管理、图遍历、时序追踪、自然语言查询
- **长期记忆** — 版本控制、反馈机制、权重调整、自动改进
- **记忆生命周期** — 差异化半衰期、冷热分层、软删除/恢复、自动归档
- **混合搜索** — 语义+BM25+实体+时间衰减融合排序、权重可调
- **Agent 对话** — 带记忆上下文的 LLM 对话、工具调用、流式输出
- **可观测性** — 仪表盘指标、质量评估、链路追踪
""",
    version="0.2.0",
    lifespan=lifespan,
    openapi_tags=[
        {"name": "agent", "description": "Agent 对话与工具调用"},
        {"name": "memory-graph", "description": "知识图谱记忆管理"},
        {"name": "sessions", "description": "会话管理"},
        {"name": "auth", "description": "认证与授权"},
    ],
)


# ------------------------------------------------------------------
# CORS
# ------------------------------------------------------------------
if settings.CORS_ORIGINS:
    _allowed_origins = [o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()]
else:
    _allowed_origins = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:4173",
    ]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ------------------------------------------------------------------
# Prometheus 指标（自动 HTTP 指标 + /metrics 端点）
# ------------------------------------------------------------------
if settings.ENABLE_METRICS:
    try:
        from prometheus_fastapi_instrumentator import Instrumentator

        Instrumentator(
            should_group_status_codes=True,
            should_ignore_untemplated=True,
            should_respect_env_var=False,
        ).instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)
    except Exception as e:
        import sys
        print(f"[WARN] Prometheus metrics init failed: {e}", file=sys.stderr)


# ------------------------------------------------------------------
# Request ID + 版本响应头 中间件
# 中间件顺序（外 → 内）：CORS → Prometheus → Request ID → Version Headers → Routers
# 注：Starlette 中后注册的中间件更靠外，故先注册 Version Headers 再注册 Request ID。
# ------------------------------------------------------------------
@app.middleware("http")
async def version_headers_middleware(request: Request, call_next):
    """为每个响应注入 API 版本与稳定性头，DEPRECATED 端点追加 Deprecation/Sunset。"""
    response = await call_next(request)
    stability = resolve_stability(request.url.path)
    response.headers["API-Version"] = API_VERSION
    response.headers["API-Stability"] = stability.value
    if stability == EndpointStability.DEPRECATED:
        response.headers["Deprecation"] = "true"
        sunset = resolve_sunset(request.url.path)
        if sunset:
            response.headers["Sunset"] = sunset
    return response


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    """生成/透传 Request ID，绑定 structlog context，并回写 X-Request-ID 头。"""
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
    token = _request_id_ctx.set(request_id)
    try:
        import structlog
        structlog.contextvars.bind_contextvars(request_id=request_id)
    except Exception:
        pass
    try:
        response = await call_next(request)
    finally:
        _request_id_ctx.reset(token)
        try:
            import structlog
            structlog.contextvars.clear_contextvars()
        except Exception:
            pass
    response.headers["X-Request-ID"] = request_id
    return response


# ------------------------------------------------------------------
# 全局异常处理器（统一 ErrorResponse 格式）
# ------------------------------------------------------------------
@app.exception_handler(AppException)
async def app_exception_handler(request: Request, exc: AppException):
    """应用异常 → 统一 ErrorResponse。"""
    trace_id = _current_trace_id()
    body = exc.to_response(trace_id=trace_id)
    logger.warning(
        "app_exception",
        code=body.code,
        message=body.message,
        path=request.url.path,
        method=request.method,
        status_code=exc.status_code,
        trace_id=trace_id,
    )
    return JSONResponse(status_code=exc.status_code, content=jsonable_encoder(body))


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """请求体/参数校验错误 → 统一 422 ErrorResponse。"""
    trace_id = _current_trace_id()
    body = ErrorResponse.build(
        ErrorCode.VALIDATION_ERROR,
        "Request validation failed",
        details=jsonable_encoder(exc.errors()),
        trace_id=trace_id,
    )
    return JSONResponse(status_code=422, content=jsonable_encoder(body))


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """兼容原有 HTTPException，转换为统一格式（保留原状态码与响应头）。"""
    trace_id = _current_trace_id()
    body = ErrorResponse.build(
        _status_to_error_code(exc.status_code),
        str(exc.detail),
        trace_id=trace_id,
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=jsonable_encoder(body),
        headers=getattr(exc, "headers", None),
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """兜底：未捕获异常 → 500 通用错误（不泄漏内部细节）。"""
    trace_id = _current_trace_id()
    logger.error(
        "unhandled_exception",
        error=str(exc),
        error_type=type(exc).__name__,
        path=request.url.path,
        method=request.method,
        trace_id=trace_id,
        exc_info=True,
    )
    body = ErrorResponse.build(
        ErrorCode.INTERNAL_ERROR,
        "Internal server error",
        trace_id=trace_id,
    )
    return JSONResponse(status_code=500, content=jsonable_encoder(body))


# ------------------------------------------------------------------
# Routers
# ------------------------------------------------------------------
app.include_router(health.router, prefix="/api/v1", tags=["health"])
app.include_router(auth.router, prefix="/api/v1/auth", tags=["authentication"])
app.include_router(memory_variables.router, prefix="/api/v1/memory", tags=["memory-variables"])
app.include_router(memory_extraction.router, prefix="/api/v1/memory/extraction", tags=["memory-extraction"])
app.include_router(memory_tables.router, prefix="/api/v1/memory/tables", tags=["memory-tables"])
app.include_router(memory_fragments.router, prefix="/api/v1/memory/fragments", tags=["memory-fragments"])
app.include_router(auto_recall.router, prefix="/api/v1/memory/recall", tags=["auto-recall"])
app.include_router(long_term_memory.router, prefix="/api/v1/memory/long-term", tags=["long-term-memory"])
app.include_router(system_integration.router, prefix="/api/v1/system", tags=["system-integration"])
app.include_router(agent.router, prefix="/api/v1/agent", tags=["agent"])
app.include_router(memory_lifecycle.router, prefix="/api/v1", tags=["memory-lifecycle"])
app.include_router(graph_memory.router, prefix="/api/v1", tags=["memory-graph"])
app.include_router(hybrid_search.router, prefix="/api/v1", tags=["hybrid-search"])
app.include_router(memory_observability.router, prefix="/api/v1", tags=["memory-observability"])
app.include_router(sessions.router, prefix="/api/v1/agent", tags=["sessions"])
app.include_router(workspace.router, prefix="/api/v1/workspaces", tags=["workspaces"])
app.include_router(webhooks.router, prefix="/api/v1/webhooks", tags=["webhooks"])
app.include_router(events.router, prefix="/api/v1/events", tags=["events"])


@app.get("/")
async def root():
    return {
        "message": "Agent Memory System API",
        "version": "0.1.0",
        "status": "running",
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=settings.HOST, port=settings.PORT)
