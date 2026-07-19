"""
任务 3.3：OpenTelemetry Span 关键路径测试

覆盖：NoOp tracer 降级、span 属性设置、异常记录、context 传播、
get_trace_id 从 span context 提取、init/shutdown 生命周期。
不依赖真实 OTLP collector。
"""
import pytest

from app.core import tracing


# ============================================================
# 1. NoOp 降级（OTel 未初始化 / 未安装）
# ============================================================

class TestNoOpTracer:
    def test_noop_span_context_manager(self):
        span = tracing._NoOpSpan()
        with span as s:
            assert s is span

    def test_noop_span_methods_are_safe(self):
        span = tracing._NoOpSpan()
        # 所有方法均为无操作，不应抛异常
        span.set_attribute("k", "v")
        span.set_status("ok")
        span.record_exception(ValueError("x"))
        span.add_event("evt", {"a": 1})
        span.end()

    def test_noop_tracer_start_span(self):
        tracer = tracing._NoOpTracer()
        span = tracer.start_span("op")
        assert isinstance(span, tracing._NoOpSpan)

    def test_noop_tracer_start_as_current_span(self):
        tracer = tracing._NoOpTracer()
        with tracer.start_as_current_span("op") as span:
            span.set_attribute("phase", "test")


# ============================================================
# 2. get_tracer 单例
# ============================================================

class TestGetTracer:
    def test_get_tracer_returns_singleton(self):
        t1 = tracing.get_tracer()
        t2 = tracing.get_tracer()
        assert t1 is t2

    def test_tracer_can_start_span(self):
        tracer = tracing.get_tracer()
        span = tracer.start_span("webhook.deliver")
        # 无论 real 还是 NoOp，都支持 set_attribute + end
        span.set_attribute("webhook.id", 1)
        span.end()


# ============================================================
# 3. init/shutdown 生命周期
# ============================================================

class TestTracingLifecycle:
    def test_init_disabled_returns_false(self, monkeypatch):
        """ENABLE_TRACING=False 时应跳过并返回 False + NoOp tracer"""
        from app.core.config import get_settings
        settings = get_settings()
        monkeypatch.setattr(settings, "ENABLE_TRACING", False, raising=False)
        # 重置全局 tracer
        tracing._tracer = None
        result = tracing.init_tracing(app=None)
        assert result is False
        assert isinstance(tracing.get_tracer(), tracing._NoOpTracer)

    def test_shutdown_is_safe(self):
        # shutdown 不应抛异常（即使 provider 无 shutdown 方法）
        tracing.shutdown_tracing()

    def test_instrument_fastapi_none_app_noop(self):
        # app=None 直接返回，不抛异常
        tracing._instrument_fastapi(None, provider=None)


# ============================================================
# 4. Span 属性 + 异常记录（通过真实 SDK，如已安装）
# ============================================================

class TestSpanAttributesAndExceptions:
    def _make_provider_tracer(self):
        """尝试用内存 SDK 构造真实 tracer；未安装则跳过"""
        try:
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import (
                SimpleSpanProcessor,
                ConsoleSpanExporter,
            )
        except ImportError:
            pytest.skip("opentelemetry sdk not installed")
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
        return provider.get_tracer("test")

    def test_span_set_attribute_and_record_exception(self):
        tracer = self._make_provider_tracer()
        with tracer.start_as_current_span("op") as span:
            span.set_attribute("event.type", "memory.created")
            try:
                raise RuntimeError("boom")
            except RuntimeError as e:
                span.record_exception(e)
        # 能执行到此说明未抛出

    def test_get_trace_id_within_active_span(self):
        """在活跃 span 内，errors.get_trace_id 应返回该 span 的 trace_id"""
        from app.core.errors import get_trace_id
        try:
            from opentelemetry import trace
        except ImportError:
            pytest.skip("opentelemetry not installed")
        tracer = self._make_provider_tracer()
        with tracer.start_as_current_span("op"):
            tid = get_trace_id()
            span = trace.get_current_span()
            ctx = span.get_span_context()
            if ctx and ctx.trace_id:
                assert tid == format(ctx.trace_id, "032x")
            else:
                assert tid  # 至少非空


# ============================================================
# 5. get_trace_id 降级为 UUID4
# ============================================================

class TestGetTraceIdFallback:
    def test_returns_nonempty_without_active_span(self):
        from app.core.errors import get_trace_id
        tid = get_trace_id()
        assert isinstance(tid, str)
        assert len(tid) > 0

    def test_distinct_uuids_when_no_span(self):
        """无活跃 span 时应降级为随机 UUID4（两次调用不同）"""
        from app.core.errors import get_trace_id
        try:
            from opentelemetry import trace
            span = trace.get_current_span()
            ctx = span.get_span_context() if span else None
            has_active = bool(ctx and ctx.trace_id)
        except ImportError:
            has_active = False
        if has_active:
            pytest.skip("active span present, fallback not exercised")
        assert get_trace_id() != get_trace_id()
