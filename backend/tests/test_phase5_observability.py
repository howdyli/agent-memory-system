"""
Phase 5 测试：OTel 追踪 + Prometheus 指标 + Service Span 埋点
"""
import pytest
from unittest.mock import MagicMock, patch


# ============================================================
# 1. tracing.py 初始化测试
# ============================================================

class TestTracingInit:
    """init_tracing / get_tracer / shutdown_tracing 测试"""

    def test_init_tracing_disabled(self):
        """ENABLE_TRACING=False 时返回 False，使用 NoOp tracer"""
        import app.core.tracing as tracing_mod
        # 重置全局状态
        tracing_mod._tracer = None

        mock_settings = MagicMock()
        mock_settings.ENABLE_TRACING = False

        with patch("app.core.config.get_settings", return_value=mock_settings):
            result = tracing_mod.init_tracing(app=None)

        assert result is False
        assert isinstance(tracing_mod._tracer, tracing_mod._NoOpTracer)

    def test_init_tracing_enabled_otel_missing(self):
        """ENABLE_TRACING=True 但 OTel 未安装时降级为 NoOp"""
        import app.core.tracing as tracing_mod
        tracing_mod._tracer = None

        mock_settings = MagicMock()
        mock_settings.ENABLE_TRACING = True
        mock_settings.OTLP = MagicMock()
        mock_settings.OTLP.ENDPOINT = "http://localhost:4317"
        mock_settings.LOG_LEVEL = "INFO"

        # 模拟 opentelemetry 导入失败
        import builtins
        original_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name.startswith("opentelemetry"):
                raise ImportError(f"Mock: {name} not installed")
            return original_import(name, *args, **kwargs)

        with patch("app.core.config.get_settings", return_value=mock_settings), \
             patch("builtins.__import__", side_effect=mock_import):
            result = tracing_mod.init_tracing(app=None)

        assert result is False
        assert isinstance(tracing_mod._tracer, tracing_mod._NoOpTracer)

    def test_get_tracer_returns_noop_when_not_initialized(self):
        """未初始化时 get_tracer 返回 NoOp tracer（OTel 未安装场景）"""
        import app.core.tracing as tracing_mod
        tracing_mod._tracer = None

        # 模拟 opentelemetry 导入失败
        import builtins
        original_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "opentelemetry":
                raise ImportError("Mock: opentelemetry not installed")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import):
            tracer = tracing_mod.get_tracer()

        assert isinstance(tracer, tracing_mod._NoOpTracer)

    def test_shutdown_tracing_no_error(self):
        """shutdown_tracing 在任何情况下都不应抛出异常"""
        import app.core.tracing as tracing_mod
        tracing_mod._tracer = None
        # 不应抛异常
        tracing_mod.shutdown_tracing()


# ============================================================
# 2. NoOp Tracer/Span 测试
# ============================================================

class TestNoOpImplementation:
    """NoOp tracer/span 接口完整性测试"""

    def test_noop_tracer_start_as_current_span(self):
        from app.core.tracing import _NoOpTracer, _NoOpSpan
        tracer = _NoOpTracer()
        span = tracer.start_as_current_span("test")
        assert isinstance(span, _NoOpSpan)

    def test_noop_tracer_start_span(self):
        from app.core.tracing import _NoOpTracer, _NoOpSpan
        tracer = _NoOpTracer()
        span = tracer.start_span("test")
        assert isinstance(span, _NoOpSpan)

    def test_noop_span_context_manager(self):
        from app.core.tracing import _NoOpSpan
        span = _NoOpSpan()
        with span as s:
            assert s is span

    def test_noop_span_methods_no_error(self):
        """NoOp span 所有方法调用不应抛异常"""
        from app.core.tracing import _NoOpSpan
        span = _NoOpSpan()
        span.set_attribute("key", "value")
        span.set_status("ok")
        span.record_exception(Exception("test"))
        span.add_event("test_event", {"key": "value"})


# ============================================================
# 3. Prometheus 指标测试
# ============================================================

class TestMetrics:
    """Phase 5 新增指标可正常 inc/observe/set"""

    def test_metrics_enabled(self):
        """prometheus_client 已安装"""
        from app.core import metrics
        assert metrics.METRICS_ENABLED is True

    def test_event_bus_published_total(self):
        from app.core.metrics import event_bus_published_total
        event_bus_published_total.labels(event_type="memory.created").inc()

    def test_event_bus_subscribers(self):
        from app.core.metrics import event_bus_subscribers
        event_bus_subscribers.set(5)

    def test_webhook_delivery_latency(self):
        from app.core.metrics import webhook_delivery_latency_seconds
        webhook_delivery_latency_seconds.observe(0.25)

    def test_memory_table_operations(self):
        from app.core.metrics import memory_table_operations_total
        memory_table_operations_total.labels(operation="create_table").inc()

    def test_graph_operations(self):
        from app.core.metrics import graph_operations_total
        graph_operations_total.labels(operation="create", kind="entity").inc()

    def test_memory_lifecycle_actions(self):
        from app.core.metrics import memory_lifecycle_actions_total
        memory_lifecycle_actions_total.labels(action="decay").inc()

    def test_context_compression_latency(self):
        from app.core.metrics import context_compression_latency_seconds
        context_compression_latency_seconds.observe(0.5)

    def test_hybrid_search_latency(self):
        from app.core.metrics import hybrid_search_latency_seconds
        hybrid_search_latency_seconds.observe(0.1)

    def test_hybrid_search_result_count(self):
        from app.core.metrics import hybrid_search_result_count
        hybrid_search_result_count.observe(10)


# ============================================================
# 4. Service Span 埋点验证
# ============================================================

class TestServiceSpanInstrumentation:
    """验证 service 层核心方法使用了 tracer span"""

    def test_auto_recall_service_has_span(self):
        """auto_recall_service.auto_recall 包含 tracing span"""
        import inspect
        from app.services import auto_recall_service
        source = inspect.getsource(auto_recall_service.auto_recall)
        assert "tracer" in source or "start_span" in source or "start_as_current_span" in source

    def test_fragment_service_has_span(self):
        """memory_fragment_service.create_fragment 包含 tracing span"""
        import inspect
        from app.services import memory_fragment_service
        source = inspect.getsource(memory_fragment_service.create_fragment)
        assert "tracer" in source or "start_span" in source or "start_as_current_span" in source

    def test_variable_service_has_span(self):
        """memory_variable_service.set_memory_variable 包含 tracing span"""
        import inspect
        from app.services import memory_variable_service
        source = inspect.getsource(memory_variable_service.set_memory_variable)
        assert "tracer" in source or "start_span" in source or "start_as_current_span" in source

    def test_hybrid_search_service_has_span(self):
        """hybrid_search_service.hybrid_search 包含 tracing span"""
        import inspect
        from app.services import hybrid_search_service
        source = inspect.getsource(hybrid_search_service.hybrid_search)
        assert "tracer" in source or "start_span" in source or "start_as_current_span" in source

    def test_webhook_service_has_span(self):
        """webhook_service.deliver_webhook 包含 tracing span"""
        import inspect
        from app.services import webhook_service
        source = inspect.getsource(webhook_service.deliver_webhook)
        assert "tracer" in source or "start_span" in source or "start_as_current_span" in source

    def test_graph_service_has_span(self):
        """graph_memory_service.ensure_entity 包含 tracing span"""
        import inspect
        from app.services import graph_memory_service
        source = inspect.getsource(graph_memory_service.ensure_entity)
        assert "tracer" in source or "start_span" in source or "start_as_current_span" in source


# ============================================================
# 5. 部署配置文件验证
# ============================================================

class TestDeployConfig:
    """验证部署配置文件存在且格式正确"""

    def test_otel_collector_config_exists(self):
        import os
        path = os.path.join(os.path.dirname(__file__), "..", "..", "deploy", "otel-collector.yaml")
        assert os.path.exists(path), "deploy/otel-collector.yaml should exist"

    def test_prometheus_alerts_exist(self):
        import os
        path = os.path.join(os.path.dirname(__file__), "..", "..", "deploy", "prometheus", "alerts.yml")
        assert os.path.exists(path), "deploy/prometheus/alerts.yml should exist"

    def test_grafana_dashboard_exists(self):
        import os
        path = os.path.join(os.path.dirname(__file__), "..", "..", "deploy", "grafana", "dashboards", "memory-system-overview.json")
        assert os.path.exists(path), "Grafana dashboard JSON should exist"

    def test_grafana_dashboard_valid_json(self):
        import os, json
        path = os.path.join(os.path.dirname(__file__), "..", "..", "deploy", "grafana", "dashboards", "memory-system-overview.json")
        with open(path) as f:
            data = json.load(f)
        assert "panels" in data
        assert len(data["panels"]) >= 10

    def test_prometheus_alerts_valid_yaml(self):
        import os
        try:
            import yaml
        except ImportError:
            pytest.skip("PyYAML not installed")
        path = os.path.join(os.path.dirname(__file__), "..", "..", "deploy", "prometheus", "alerts.yml")
        with open(path) as f:
            data = yaml.safe_load(f)
        assert "groups" in data
        assert len(data["groups"]) >= 1

    def test_docker_compose_has_monitoring_services(self):
        import os
        try:
            import yaml
        except ImportError:
            pytest.skip("PyYAML not installed")
        path = os.path.join(os.path.dirname(__file__), "..", "..", "docker-compose.prod.yml")
        with open(path) as f:
            data = yaml.safe_load(f)
        services = data.get("services", {})
        assert "prometheus" in services, "prometheus service should be defined"
        assert "grafana" in services, "grafana service should be defined"
        assert "otel-collector" in services, "otel-collector service should be defined"
        assert "jaeger" in services, "jaeger service should be defined"

    def test_docker_compose_backend_has_tracing_env(self):
        import os
        try:
            import yaml
        except ImportError:
            pytest.skip("PyYAML not installed")
        path = os.path.join(os.path.dirname(__file__), "..", "..", "docker-compose.prod.yml")
        with open(path) as f:
            data = yaml.safe_load(f)
        backend_env = data["services"]["backend"].get("environment", [])
        env_str = " ".join(backend_env)
        assert "ENABLE_TRACING" in env_str, "backend should have ENABLE_TRACING env var"
        assert "OTLP_ENDPOINT" in env_str, "backend should have OTLP_ENDPOINT env var"
