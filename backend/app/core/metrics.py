"""
Prometheus 指标定义模块

定义全局业务指标（Counter/Histogram/Gauge），
由 service 层在关键路径埋点调用。
"""
try:
    from prometheus_client import Counter, Histogram, Gauge

    # ============================================================
    # 记忆操作计数
    # ============================================================
    memory_operations_total = Counter(
        "agent_memory_operations_total",
        "Total memory operations",
        ["operation", "memory_type"],
    )

    # ============================================================
    # 召回延迟
    # ============================================================
    memory_recall_latency_seconds = Histogram(
        "agent_memory_recall_latency_seconds",
        "Memory recall latency",
        ["memory_type"],
        buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
    )

    # ============================================================
    # 召回命中率
    # ============================================================
    memory_recall_hit_rate = Gauge(
        "agent_memory_recall_hit_rate",
        "Memory recall hit rate (0-1)",
    )

    # ============================================================
    # 向量搜索延迟
    # ============================================================
    vector_search_latency_seconds = Histogram(
        "agent_memory_vector_search_latency_seconds",
        "Vector search latency",
        ["backend"],
    )

    # ============================================================
    # 活跃记忆数
    # ============================================================
    active_memories_gauge = Gauge(
        "agent_memory_active_count",
        "Active memory count",
        ["memory_type"],
    )

    # ============================================================
    # LLM token 消耗
    # ============================================================
    llm_tokens_used_total = Counter(
        "agent_memory_llm_tokens_total",
        "LLM tokens used",
        ["operation", "token_type"],  # token_type: prompt|completion
    )

    # ============================================================
    # Webhook 投递
    # ============================================================
    webhook_deliveries_total = Counter(
        "agent_memory_webhook_deliveries_total",
        "Webhook deliveries",
        ["status"],  # success|failed
    )

    # ============================================================
    # Phase 4/5: 事件总线指标
    # ============================================================
    event_bus_published_total = Counter(
        "agent_memory_events_published_total",
        "Events published to event bus",
        ["event_type"],
    )

    event_bus_subscribers = Gauge(
        "agent_memory_event_subscribers",
        "Current number of event bus subscribers",
    )

    # ============================================================
    # Phase 5: Webhook 投递延迟
    # ============================================================
    webhook_delivery_latency_seconds = Histogram(
        "agent_memory_webhook_delivery_seconds",
        "Webhook delivery latency",
        buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
    )

    # ============================================================
    # Phase 5: 记忆表操作
    # ============================================================
    memory_table_operations_total = Counter(
        "agent_memory_table_operations_total",
        "Memory table operations",
        ["operation"],  # create_table|drop_table|add_record|query
    )

    # ============================================================
    # Phase 5: 图谱操作
    # ============================================================
    graph_operations_total = Counter(
        "agent_memory_graph_operations_total",
        "Graph memory operations",
        ["operation", "kind"],  # operation: create|query|merge, kind: entity|relationship
    )

    # ============================================================
    # Phase 5: 生命周期操作
    # ============================================================
    memory_lifecycle_actions_total = Counter(
        "agent_memory_lifecycle_actions_total",
        "Memory lifecycle actions",
        ["action"],  # decay|evict|merge|cold_mark|restore
    )

    # ============================================================
    # Phase 5: 上下文压缩延迟
    # ============================================================
    context_compression_latency_seconds = Histogram(
        "agent_memory_context_compression_seconds",
        "Context compression latency",
        buckets=(0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
    )

    # ============================================================
    # Phase 5: 混合检索指标
    # ============================================================
    hybrid_search_latency_seconds = Histogram(
        "agent_memory_hybrid_search_seconds",
        "Hybrid search latency",
        buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5),
    )

    hybrid_search_result_count = Histogram(
        "agent_memory_hybrid_search_result_count",
        "Hybrid search result count",
        buckets=(0, 1, 5, 10, 20, 50),
    )

    METRICS_ENABLED = True

except ImportError:
    # prometheus-client 未安装时提供空操作 stub
    METRICS_ENABLED = False

    class _Stub:
        """无操作指标 stub"""
        def labels(self, *args, **kwargs):
            return self
        def inc(self, *args, **kwargs):
            pass
        def observe(self, *args, **kwargs):
            pass
        def set(self, *args, **kwargs):
            pass

    memory_operations_total = _Stub()
    memory_recall_latency_seconds = _Stub()
    memory_recall_hit_rate = _Stub()
    vector_search_latency_seconds = _Stub()
    active_memories_gauge = _Stub()
    llm_tokens_used_total = _Stub()
    webhook_deliveries_total = _Stub()
    event_bus_published_total = _Stub()
    event_bus_subscribers = _Stub()
    webhook_delivery_latency_seconds = _Stub()
    memory_table_operations_total = _Stub()
    graph_operations_total = _Stub()
    memory_lifecycle_actions_total = _Stub()
    context_compression_latency_seconds = _Stub()
    hybrid_search_latency_seconds = _Stub()
    hybrid_search_result_count = _Stub()
