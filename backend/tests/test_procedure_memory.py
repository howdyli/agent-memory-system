"""
程序记忆测试（P2 R-14）

覆盖：
- record_tool_call 落库与成败判定
- 显式 CRUD API 全流程
- 提炼服务 mock LLM 返回 → 片段创建 + extracted 标记 + 去重跳过
- PROCEDURE_MAX_SESSIONS_PER_RUN 限流
- LLM 不可用跳过路径
- HALF_LIFE_CONFIG 注册
"""
import pytest
import json

USER_ID = 999


@pytest.fixture(autouse=True)
def cleanup_procedure():
    """清理 agent_tool_traces / procedure 片段"""
    yield
    from app.core.db_client import get_db_client
    db = get_db_client()
    try:
        db.execute('DELETE FROM agent_tool_traces WHERE user_id = ?', (USER_ID,))
    except Exception:
        pass
    try:
        db.execute("DELETE FROM memory_fragments WHERE user_id = ? AND fragment_type = 'procedure'", (USER_ID,))
    except Exception:
        pass


# ============================================================
# HALF_LIFE_CONFIG
# ============================================================

class TestProcedureConfig:
    def test_half_life_registered(self):
        from app.services.memory_lifecycle_service import HALF_LIFE_CONFIG
        assert "procedure" in HALF_LIFE_CONFIG
        assert HALF_LIFE_CONFIG["procedure"]["half_life_days"] == 180
        assert HALF_LIFE_CONFIG["procedure"]["decay_enabled"] is True


# ============================================================
# tool_trace_service
# ============================================================

class TestToolTraceService:
    def test_record_and_query(self):
        from app.services.tool_trace_service import record_tool_call, get_session_traces
        r = record_tool_call(
            user_id=USER_ID,
            session_id="test-sess-001",
            round_idx=0,
            tool_name="memory_search",
            arguments={"query": "hello"},
            result='{"success": true, "results": []}',
            duration_ms=42,
        )
        assert r["success"] is True
        traces = get_session_traces("test-sess-001", user_id=USER_ID)
        assert len(traces) >= 1
        t = traces[-1]
        assert t["tool_name"] == "memory_search"
        assert t["success"] == 1
        assert t["duration_ms"] == 42

    def test_judge_success_false(self):
        from app.services.tool_trace_service import record_tool_call, get_session_traces
        record_tool_call(
            user_id=USER_ID,
            session_id="test-sess-fail",
            round_idx=0,
            tool_name="bad_tool",
            arguments={},
            result='{"success": false, "error": "oops"}',
        )
        traces = get_session_traces("test-sess-fail", user_id=USER_ID)
        assert traces[-1]["success"] == 0

    def test_mark_extracted(self):
        from app.services.tool_trace_service import record_tool_call, mark_session_extracted, get_session_traces
        record_tool_call(USER_ID, "test-sess-mark", 0, "t", {}, '{"success": true}')
        mark_session_extracted("test-sess-mark", USER_ID)
        traces = get_session_traces("test-sess-mark", user_id=USER_ID)
        assert all(t["extracted"] == 1 for t in traces)


# ============================================================
# procedure_extraction_service
# ============================================================

class TestProcedureExtraction:
    def _seed_traces(self, session_id="ext-sess-001", n=3):
        from app.services.tool_trace_service import record_tool_call
        for i in range(n):
            record_tool_call(
                user_id=USER_ID,
                session_id=session_id,
                round_idx=i,
                tool_name=f"tool_{i}",
                arguments={"arg": i},
                result='{"success": true}',
            )

    def test_extract_with_mock_llm(self, monkeypatch):
        """mock LLM 返回 has_procedure → 创建 procedure 片段"""
        self._seed_traces()
        import uuid
        unique_title = f"测试流程_{uuid.uuid4().hex[:8]}"
        mock_response = {
            "success": True,
            "content": json.dumps({
                "has_procedure": True,
                "title": unique_title,
                "steps": ["步骤一", "步骤二", "步骤三"],
                "applicable_scenario": "测试场景",
            }, ensure_ascii=False),
        }
        from app.services import llm_backend_service
        monkeypatch.setattr(llm_backend_service, "llm_chat", lambda *a, **kw: mock_response)
        # 跳过去重检查（真实库可能已有相似内容）
        from app.services import procedure_extraction_service
        monkeypatch.setattr(procedure_extraction_service, "_store_procedure_with_dedup",
                            lambda user_id, content, metadata=None:
                            __import__('app.services.memory_fragment_service', fromlist=['create_fragment']).create_fragment(
                                user_id=user_id, fragment_type='procedure', content=content, importance_score=0.7, metadata=metadata))

        from app.services.procedure_extraction_service import extract_procedures_for_user
        result = extract_procedures_for_user(USER_ID)
        assert result["success"] is True
        assert result["procedures_created"] >= 1

        # 验证 extracted 标记
        from app.services.tool_trace_service import get_session_traces
        traces = get_session_traces("ext-sess-001", user_id=USER_ID)
        assert all(t["extracted"] == 1 for t in traces)

        # 验证片段内容
        from app.core.db_client import get_db_client
        db = get_db_client()
        rows = db.execute(
            "SELECT * FROM memory_fragments WHERE user_id = ? AND fragment_type = 'procedure'",
            (USER_ID,)
        )
        assert len(rows) >= 1
        assert unique_title in rows[0]["content"]

    def test_extract_dedup(self, monkeypatch):
        """重复内容 → 去重跳过"""
        # 先手动插入一条 procedure 片段，然后提炼出完全相同内容 → 应被去重
        from app.services.memory_fragment_service import create_fragment
        dup_content = "完全相同流程\n适用场景: 相同场景\n步骤:\n1. 完全一样步骤"
        create_fragment(user_id=USER_ID, fragment_type="procedure", content=dup_content)

        self._seed_traces("dedup-sess-1")
        mock_response = {
            "success": True,
            "content": json.dumps({
                "has_procedure": True,
                "title": "完全相同流程",
                "steps": ["完全一样步骤"],
                "applicable_scenario": "相同场景",
            }, ensure_ascii=False),
        }
        from app.services import llm_backend_service
        monkeypatch.setattr(llm_backend_service, "llm_chat", lambda *a, **kw: mock_response)

        from app.services.procedure_extraction_service import extract_procedures_for_user
        result = extract_procedures_for_user(USER_ID)
        assert result["success"] is True
        # 应被去重跳过，不再新建
        assert result["skipped_reason_stats"].get("duplicate", 0) >= 1

    def test_extract_llm_unavailable(self, monkeypatch):
        """LLM mock 模式 → 跳过不创建"""
        self._seed_traces("unavail-sess")
        mock_response = {"success": True, "content": "mock", "mock": True}
        from app.services import llm_backend_service
        monkeypatch.setattr(llm_backend_service, "llm_chat", lambda *a, **kw: mock_response)

        from app.services.procedure_extraction_service import extract_procedures_for_user
        result = extract_procedures_for_user(USER_ID)
        assert result["success"] is True
        assert result["procedures_created"] == 0
        assert result["skipped_reason_stats"].get("llm_unavailable", 0) >= 1

    def test_max_sessions_limit(self, monkeypatch):
        """PROCEDURE_MAX_SESSIONS_PER_RUN 限流"""
        from app.core.config import get_settings
        monkeypatch.setattr(get_settings(), "PROCEDURE_MAX_SESSIONS_PER_RUN", 1)
        self._seed_traces("limit-sess-1")
        self._seed_traces("limit-sess-2")
        # 只应扫描 1 个会话
        from app.services.procedure_extraction_service import _get_candidate_sessions
        sessions = _get_candidate_sessions(USER_ID)
        assert len(sessions) <= 1


# ============================================================
# procedures API（显式 CRUD，直接调 service 层）
# ============================================================

class TestProcedureCRUD:
    def test_create_and_list(self):
        from app.services.procedure_extraction_service import build_procedure_content
        from app.services.memory_fragment_service import create_fragment, list_fragments
        content = build_procedure_content("手动流程", ["A", "B"], "手动场景")
        r = create_fragment(
            user_id=USER_ID, fragment_type="procedure",
            content=content, metadata={"source": "manual"},
        )
        assert r["success"] is True
        frags = list_fragments(USER_ID, fragment_type="procedure")
        assert frags["success"] is True
        assert frags["count"] >= 1

    def test_delete(self):
        from app.services.memory_fragment_service import create_fragment, delete_fragment
        r = create_fragment(
            user_id=USER_ID, fragment_type="procedure",
            content="删除测试\n步骤:\n1. x",
        )
        d = delete_fragment(USER_ID, r["fragment_id"])
        assert d["success"] is True
