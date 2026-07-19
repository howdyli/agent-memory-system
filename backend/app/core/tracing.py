"""
Phase 5: OpenTelemetry 链路追踪模块

提供 OTel 初始化函数，自动 instrument FastAPI / SQLAlchemy / Redis / httpx。
当 settings.ENABLE_TRACING=False 时完全跳过（零开销）。
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# 全局 tracer（未初始化时为 NoOp）
_tracer = None


def get_tracer(name: str = "agent_memory") -> "opentelemetry.trace.Tracer":
    """获取全局 tracer 实例"""
    global _tracer
    if _tracer is None:
        try:
            from opentelemetry import trace
            _tracer = trace.get_tracer(name)
        except ImportError:
            _tracer = _NoOpTracer()
    return _tracer


def init_tracing(app=None) -> bool:
    """
    初始化 OpenTelemetry 链路追踪。

    自动 instrument：
    - FastAPI（需要传入 app 实例）
    - SQLAlchemy
    - Redis
    - httpx

    Args:
        app: FastAPI 实例（可选，传入后自动 instrument HTTP 层）

    Returns:
        是否成功初始化
    """
    global _tracer

    from app.core.config import get_settings
    settings = get_settings()

    if not settings.ENABLE_TRACING:
        logger.info("Tracing disabled (ENABLE_TRACING=false)")
        _tracer = _NoOpTracer()
        return False

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.resources import Resource, SERVICE_NAME, SERVICE_VERSION
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

        # Resource 属性
        resource = Resource.create({
            SERVICE_NAME: "agent-memory-system",
            SERVICE_VERSION: "0.1.0",
            "deployment.environment": "production" if settings.LOG_LEVEL == "WARNING" else "development",
        })

        # TracerProvider
        provider = TracerProvider(resource=resource)

        # OTLP Exporter
        otlp_endpoint = settings.OTLP_ENDPOINT or "http://localhost:4317"
        exporter = OTLPSpanExporter(endpoint=otlp_endpoint, insecure=True)
        provider.add_span_processor(BatchSpanProcessor(exporter))

        # 设置为全局 provider
        trace.set_tracer_provider(provider)
        _tracer = provider.get_tracer("agent_memory")

        # ── 自动 instrumentation ──
        _instrument_fastapi(app, provider)
        _instrument_sqlalchemy()
        _instrument_redis()
        _instrument_httpx()

        logger.info(f"OTel tracing initialized -> {otlp_endpoint}")
        return True

    except ImportError as e:
        logger.warning(f"OTel packages not installed, tracing disabled: {e}")
        _tracer = _NoOpTracer()
        return False
    except Exception as e:
        logger.error(f"Failed to initialize OTel tracing: {e}")
        _tracer = _NoOpTracer()
        return False


def _instrument_fastapi(app, provider):
    """Instrument FastAPI 应用"""
    if app is None:
        return
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        FastAPIInstrumentor.instrument_app(app, tracer_provider=provider)
        logger.debug("FastAPI instrumented")
    except ImportError:
        logger.debug("opentelemetry-instrumentation-fastapi not installed")
    except Exception as e:
        logger.warning(f"FastAPI instrumentation failed: {e}")


def _instrument_sqlalchemy():
    """Instrument SQLAlchemy"""
    try:
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
        SQLAlchemyInstrumentor().instrument()
        logger.debug("SQLAlchemy instrumented")
    except ImportError:
        logger.debug("opentelemetry-instrumentation-sqlalchemy not installed")
    except Exception as e:
        logger.warning(f"SQLAlchemy instrumentation failed: {e}")


def _instrument_redis():
    """Instrument Redis"""
    try:
        from opentelemetry.instrumentation.redis import RedisInstrumentor
        RedisInstrumentor().instrument()
        logger.debug("Redis instrumented")
    except ImportError:
        logger.debug("opentelemetry-instrumentation-redis not installed")
    except Exception as e:
        logger.warning(f"Redis instrumentation failed: {e}")


def _instrument_httpx():
    """Instrument httpx"""
    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        HTTPXClientInstrumentor().instrument()
        logger.debug("httpx instrumented")
    except ImportError:
        logger.debug("opentelemetry-instrumentation-httpx not installed")
    except Exception as e:
        logger.warning(f"httpx instrumentation failed: {e}")


def shutdown_tracing():
    """关闭 OTel，刷新所有 span"""
    try:
        from opentelemetry import trace
        provider = trace.get_tracer_provider()
        if hasattr(provider, "shutdown"):
            provider.shutdown()
            logger.info("OTel tracing shut down")
    except Exception:
        pass


# ============================================================
# NoOp 实现（OTel 未安装时使用）
# ============================================================

class _NoOpSpan:
    """无操作 span"""
    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def set_attribute(self, key, value):
        pass

    def set_status(self, status):
        pass

    def record_exception(self, exception):
        pass

    def add_event(self, name, attributes=None):
        pass

    def end(self):
        pass


class _NoOpTracer:
    """无操作 tracer"""
    def start_as_current_span(self, name, **kwargs):
        return _NoOpSpan()

    def start_span(self, name, **kwargs):
        return _NoOpSpan()
