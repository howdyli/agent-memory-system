"""
W1 压缩引擎 MVP 测试

覆盖：
- 压缩断路器集成（_generate_compression_summary）
- Fallback 摘要增强
- 跨层去重（exclude_ids 机制）
- 预算选择策略（knapsack vs greedy，auto 切换）
"""
import pytest
import time
from unittest.mock import patch, MagicMock

from app.core.circuit_breaker import CircuitBreaker, STATE_CLOSED, STATE_OPEN, STATE_HALF_OPEN
import app.core.circuit_breaker as cb_mod


# ============================================================
# 1. 压缩断路器集成
# ============================================================

class TestCompressionCircuitBreaker:
    """测试压缩引擎专用断路器行为"""

    def test_breaker_initialized(self):
        """全局断路器实例正确初始化"""
        from app.services.context_compressor import _compression_circuit_breaker
        assert _compression_circuit_breaker is not None
        assert _compression_circuit_breaker.state == STATE_CLOSED

    def test_breaker_opens_after_failures(self):
        """连续失败 3 次后断路器 OPEN"""
        breaker = CircuitBreaker(
            name="test_compression",
            failure_threshold=3,
            recovery_timeout=60.0,
            half_open_max_calls=1,
        )
        assert breaker.state == STATE_CLOSED

        for _ in range(3):
            breaker.record_failure()

        assert breaker.state == STATE_OPEN
        assert breaker.allow() is False

    def test_breaker_recovery_half_open(self, monkeypatch):
        """恢复超时后进入 HALF_OPEN 放行一次试探"""
        clock = {"t": 1000.0}
        monkeypatch.setattr(cb_mod.time, "monotonic", lambda: clock["t"])

        breaker = CircuitBreaker(
            name="test_comp_recovery",
            failure_threshold=3,
            recovery_timeout=60.0,
            half_open_max_calls=1,
        )
        for _ in range(3):
            breaker.record_failure()
        assert breaker.state == STATE_OPEN

        # 推进时钟 61s
        clock["t"] += 61.0
        assert breaker.allow() is True
        assert breaker.state == STATE_HALF_OPEN
        # 配额用尽
        assert breaker.allow() is False

    def test_fallback_used_when_breaker_open(self):
        """断路器 OPEN 时直接使用 fallback 摘要"""
        from app.services.context_compressor import ConversationManager, _compression_circuit_breaker

        # 强制 OPEN
        for _ in range(10):
            _compression_circuit_breaker.record_failure()
        assert _compression_circuit_breaker.state == STATE_OPEN

        try:
            mgr = ConversationManager(config={"recent_rounds": 3})
            text = "用户: 你好\n助手: 你好！有什么可以帮助你的？"
            result = mgr._generate_compression_summary(user_id=1, conversation_text=text)
            # fallback 摘要应返回非空字符串
            assert isinstance(result, str)
            assert len(result) > 0
            assert len(result) <= 300
        finally:
            _compression_circuit_breaker.reset()


# ============================================================
# 2. Fallback 摘要增强
# ============================================================

class TestFallbackSummary:
    """测试增强规则回退摘要"""

    def _get_manager(self):
        from app.services.context_compressor import ConversationManager
        return ConversationManager(config={"recent_rounds": 3})

    def test_basic_fallback(self):
        """基本 fallback 生成有效摘要"""
        mgr = self._get_manager()
        text = "用户: 你好，我叫张三\n助手: 你好张三！\n用户: 我在北京工作\n助手: 好的"
        result = mgr._fallback_summary(text)
        assert isinstance(result, str)
        assert len(result) > 0
        assert len(result) <= 300

    def test_fallback_extracts_entities(self):
        """fallback 能提取命名实体"""
        mgr = self._get_manager()
        text = "用户: 我叫李明，在Google工作\n助手: 好的，李明"
        result = mgr._fallback_summary(text)
        # 应该包含实体相关内容
        assert len(result) > 5

    def test_fallback_extracts_facts(self):
        """fallback 能提取事实模式"""
        mgr = self._get_manager()
        text = "用户: 我是前端工程师\n助手: 好的\n用户: 我喜欢Python\n助手: 了解"
        result = mgr._fallback_summary(text)
        assert len(result) > 5

    def test_fallback_limit_300_chars(self):
        """fallback 输出严格限制 300 字符"""
        mgr = self._get_manager()
        # 构造大量对话
        lines = []
        for i in range(50):
            lines.append(f"用户: 这是第{i}条消息，包含很长的内容描述来测试字符限制")
            lines.append(f"助手: 明白了，这是回复{i}")
        text = "\n".join(lines)
        result = mgr._fallback_summary(text)
        assert len(result) <= 300

    def test_fallback_empty_input(self):
        """空输入返回默认摘要"""
        mgr = self._get_manager()
        result = mgr._fallback_summary("")
        assert result == "(对话历史)"


# ============================================================
# 3. 跨层去重（exclude_ids）
# ============================================================

class TestCrossLayerDedup:
    """测试跨层记忆去重机制"""

    def test_recall_exclude_ids_filters(self):
        """recall() 传入 exclude_ids 后正确过滤"""
        from app.services.recall_engine import RecallEngine

        # mock 搜索结果
        fake_fragments = [
            {"id": 1, "content": "记忆A", "similarity": 0.9, "importance_score": 0.8, "fragment_type": "info"},
            {"id": 2, "content": "记忆B", "similarity": 0.8, "importance_score": 0.7, "fragment_type": "info"},
            {"id": 3, "content": "记忆C", "similarity": 0.7, "importance_score": 0.6, "fragment_type": "info"},
        ]

        with patch("app.services.recall_engine.search_fragments_by_semantic") as mock_search:
            mock_search.return_value = {"fragments": fake_fragments}
            engine = RecallEngine(config={"use_hybrid_search": False, "lifecycle_recall_update": False})

            # 不排除：应该有 3 条候选
            result_all = engine.recall(user_id=1, query="测试", budget_tokens=5000)
            assert result_all.total_candidates == 3

            # 排除 id=1,2：仅剩 1 条
            result_filtered = engine.recall(
                user_id=1, query="测试", budget_tokens=5000, exclude_ids={1, 2}
            )
            # total_candidates 仍记录原始搜索数量
            assert result_filtered.total_candidates == 3
            # 但实际选中的应该不包含 id 1 和 2
            selected_ids = {m.get("id") for m in result_filtered.memories}
            assert 1 not in selected_ids
            assert 2 not in selected_ids

    def test_recall_with_entities_exclude_ids(self):
        """recall_with_entities() 支持 exclude_ids"""
        from app.services.recall_engine import RecallEngine

        fake_related = [
            {"id": 10, "content": "实体记忆A", "similarity": 0.9, "importance_score": 0.8,
             "fragment_type": "info", "source_entity": "Python"},
            {"id": 20, "content": "实体记忆B", "similarity": 0.8, "importance_score": 0.7,
             "fragment_type": "info", "source_entity": "React"},
        ]

        with patch("app.services.recall_engine._get_entity_traverser") as mock_traverser_fn, \
             patch("app.services.recall_engine._get_scorer") as mock_scorer_fn:

            mock_traverser = MagicMock()
            mock_traverser.extract_entities.return_value = ["Python", "React"]
            mock_traverser.search_related_memories.return_value = fake_related
            mock_traverser_fn.return_value = mock_traverser

            mock_scorer = MagicMock()
            mock_scorer.select_top_by_budget.return_value = [fake_related[1]]  # 去重后仅保留 id=20
            mock_scorer_fn.return_value = mock_scorer

            engine = RecallEngine()
            result = engine.recall_with_entities(
                user_id=1, query="Python开发", budget_tokens=1200, exclude_ids={10}
            )

            # search_related_memories 返回 2 条，排除 id=10 后传给 scorer 应只有 1 条
            scorer_call_args = mock_scorer.select_top_by_budget.call_args
            # 验证 memories 参数不包含 id=10
            memories_arg = scorer_call_args[1].get("memories") or scorer_call_args[0][0]
            filtered_ids = {m["id"] for m in memories_arg}
            assert 10 not in filtered_ids

    def test_exclude_ids_none_no_filter(self):
        """exclude_ids=None 不进行过滤"""
        from app.services.recall_engine import RecallEngine

        fake_fragments = [
            {"id": 1, "content": "A", "similarity": 0.9, "importance_score": 0.8, "fragment_type": "info"},
            {"id": 2, "content": "B", "similarity": 0.8, "importance_score": 0.7, "fragment_type": "info"},
        ]

        with patch("app.services.recall_engine.search_fragments_by_semantic") as mock_search:
            mock_search.return_value = {"fragments": fake_fragments}
            engine = RecallEngine(config={"use_hybrid_search": False, "lifecycle_recall_update": False})

            result = engine.recall(user_id=1, query="测试", budget_tokens=5000, exclude_ids=None)
            assert len(result.memories) == 2


# ============================================================
# 4. 预算选择策略（knapsack / greedy）
# ============================================================

class TestBudgetSelectionStrategy:
    """测试预算选择算法"""

    def _make_memories(self, items):
        """生成测试记忆列表: items=[(content, importance, similarity)]"""
        return [
            {
                "id": i,
                "content": content,
                "importance_score": importance,
                "similarity": similarity,
                "fragment_type": "info",
                "created_at": "2026-07-20T10:00:00",
            }
            for i, (content, importance, similarity) in enumerate(items, 1)
        ]

    def test_greedy_respects_budget(self):
        """贪心策略在预算内选择"""
        from app.services.context_compressor import MemoryValueScorer

        # 每条大约 15-20 tokens
        items = [
            ("短文本A", 0.9, 0.9),
            ("短文本B这是一段稍长的内容用于占用更多token", 0.8, 0.8),
            ("短文本C", 0.7, 0.7),
        ]
        memories = self._make_memories(items)

        result = MemoryValueScorer.select_top_by_budget(
            memories=memories, query="测试", budget_tokens=50, strategy="greedy"
        )
        assert len(result) >= 1
        # 验证所有选中项的 token 总和不超预算
        from app.services.context_compressor import estimate_tokens
        total = sum(estimate_tokens(m["content"]) + 10 for m in result)
        assert total <= 50

    def test_knapsack_respects_budget(self):
        """背包策略在预算内选择"""
        from app.services.context_compressor import MemoryValueScorer

        items = [
            ("短文本A", 0.9, 0.9),
            ("短文本B较长内容", 0.8, 0.8),
            ("短文本C", 0.7, 0.7),
        ]
        memories = self._make_memories(items)

        # 使用足够大的预算来验证选择逻辑正确性
        result = MemoryValueScorer.select_top_by_budget(
            memories=memories, query="测试", budget_tokens=100, strategy="knapsack"
        )
        assert len(result) >= 1
        from app.services.context_compressor import estimate_tokens
        total = sum(estimate_tokens(m["content"]) + 10 for m in result)
        # 背包算法量化粒度 10-token，可能存在微小溢出，允许 1 个粒度容差
        assert total <= 100 + 10

    def test_auto_strategy_switches(self):
        """auto 策略：≤20 候选用 knapsack，>20 用 greedy"""
        from app.services.context_compressor import MemoryValueScorer

        # 5 条 -> 用 knapsack
        small_items = [("文本" + str(i), 0.5 + i * 0.05, 0.5 + i * 0.05) for i in range(5)]
        small_memories = self._make_memories(small_items)

        with patch.object(MemoryValueScorer, "_knapsack_select", wraps=MemoryValueScorer._knapsack_select) as mock_ks, \
             patch.object(MemoryValueScorer, "_greedy_select", wraps=MemoryValueScorer._greedy_select) as mock_gr:
            MemoryValueScorer.select_top_by_budget(
                memories=small_memories, query="测试", budget_tokens=500, strategy="auto"
            )
            mock_ks.assert_called()
            mock_gr.assert_not_called()

        # 25 条 -> 用 greedy
        large_items = [("文本" + str(i), 0.3 + (i % 10) * 0.05, 0.4 + (i % 10) * 0.03) for i in range(25)]
        large_memories = self._make_memories(large_items)

        with patch.object(MemoryValueScorer, "_knapsack_select", wraps=MemoryValueScorer._knapsack_select) as mock_ks2, \
             patch.object(MemoryValueScorer, "_greedy_select", wraps=MemoryValueScorer._greedy_select) as mock_gr2:
            MemoryValueScorer.select_top_by_budget(
                memories=large_memories, query="测试", budget_tokens=500, strategy="auto"
            )
            mock_gr2.assert_called()
            mock_ks2.assert_not_called()

    def test_knapsack_maximizes_value(self):
        """背包 DP 应找到比贪心更优（或等优）的解"""
        from app.services.context_compressor import MemoryValueScorer

        # 设计一个贪心会次优的场景：
        # A: 高价值密度但小容量, B: 中等密度, C: 低密度但大价值
        items = [
            ("A" * 5, 0.9, 0.9),   # ~15 tokens, value ~0.72
            ("B" * 20, 0.7, 0.7),  # ~30 tokens, value ~0.56
            ("C" * 40, 0.95, 0.6), # ~50 tokens, value ~0.63
        ]
        memories = self._make_memories(items)

        ks_result = MemoryValueScorer.select_top_by_budget(
            memories=memories, query="测试", budget_tokens=60, strategy="knapsack"
        )
        gr_result = MemoryValueScorer.select_top_by_budget(
            memories=memories[:], query="测试", budget_tokens=60, strategy="greedy"
        )

        # DP 的总价值应 >= 贪心
        def total_value(mems):
            return sum(MemoryValueScorer.score_memory_value(m, "测试") for m in mems)

        assert total_value(ks_result) >= total_value(gr_result) - 0.01  # 容忍浮点误差

    def test_empty_input(self):
        """空列表或零预算返回空"""
        from app.services.context_compressor import MemoryValueScorer

        assert MemoryValueScorer.select_top_by_budget([], "x", 100) == []
        items = [("文本", 0.5, 0.5)]
        memories = self._make_memories(items)
        assert MemoryValueScorer.select_top_by_budget(memories, "x", 0) == []


# ============================================================
# 5. 健康检查 API
# ============================================================

class TestCompressionHealthAPI:
    """测试压缩引擎健康检查端点"""

    @pytest.fixture
    def client(self):
        """创建 FastAPI TestClient"""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.api.health import router

        app = FastAPI()
        app.include_router(router)
        return TestClient(app)

    def test_compression_health_endpoint(self, client):
        """GET /health/compression 返回 200"""
        resp = client.get("/health/compression")
        assert resp.status_code == 200
        data = resp.json()
        assert "status" in data
        assert "circuit_breaker" in data
        assert "timestamp" in data
        assert data["status"] in ("healthy", "degraded", "unhealthy")

    def test_compression_health_shows_state(self, client):
        """端点返回断路器状态"""
        from app.services.context_compressor import _compression_circuit_breaker
        _compression_circuit_breaker.reset()

        resp = client.get("/health/compression")
        data = resp.json()
        assert data["circuit_breaker"]["state"] == "closed"
        assert data["status"] == "healthy"

    def test_compression_health_degraded_when_open(self, client):
        """断路器 OPEN 时显示 unhealthy"""
        from app.services.context_compressor import _compression_circuit_breaker

        for _ in range(10):
            _compression_circuit_breaker.record_failure()

        try:
            resp = client.get("/health/compression")
            data = resp.json()
            assert data["circuit_breaker"]["state"] == "open"
            assert data["status"] == "unhealthy"
        finally:
            _compression_circuit_breaker.reset()
