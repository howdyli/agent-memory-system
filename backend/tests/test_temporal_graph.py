"""
双时序知识图谱 — 时序点查询测试（R-04 收尾）

验证内容：
1. observed_at / expired_at 字段已添加到 graph_relationships 表
2. add_relationship 写入 observed_at
3. deactivate_relationship 写入 expired_at + valid_to
4. get_relationship_at_time 事件时间模式（event）查询
5. get_relationship_at_time 系统时间模式（system）查询
6. GET /api/v1/memory/graph/temporal API 端点
"""
import pytest
from datetime import datetime, timedelta

from app.core.db_client import get_db_client
from app.services.graph_memory_service import (
    add_relationship,
    deactivate_relationship,
    get_relationship_at_time,
    _ensure_graph_tables,
)

TEST_USER_ID = 999


@pytest.fixture(autouse=True)
def _clean_graph_tables():
    """每个测试前后清理图谱表中的测试数据。"""
    db = get_db_client()
    _ensure_graph_tables()
    yield
    try:
        db.execute("DELETE FROM graph_relationship_history WHERE user_id = ?", (TEST_USER_ID,))
        db.execute("DELETE FROM graph_relationships WHERE user_id = ?", (TEST_USER_ID,))
        db.execute("DELETE FROM graph_entities WHERE user_id = ?", (TEST_USER_ID,))
    except Exception:
        pass


# ============================================================
# 1. 字段存在性测试
# ============================================================

@pytest.mark.unit
class TestDualTemporalFields:
    """验证双时序字段已存在于表结构中。"""

    def test_observed_at_column_exists(self):
        """graph_relationships 表应包含 observed_at 列。"""
        db = get_db_client()
        rows = db.execute("PRAGMA table_info(graph_relationships)")
        columns = [r["name"] for r in rows]
        assert "observed_at" in columns, "graph_relationships 缺少 observed_at 列"

    def test_expired_at_column_exists(self):
        """graph_relationships 表应包含 expired_at 列。"""
        db = get_db_client()
        rows = db.execute("PRAGMA table_info(graph_relationships)")
        columns = [r["name"] for r in rows]
        assert "expired_at" in columns, "graph_relationships 缺少 expired_at 列"

    def test_history_observed_at_column_exists(self):
        """graph_relationship_history 表应包含 observed_at 列。"""
        db = get_db_client()
        rows = db.execute("PRAGMA table_info(graph_relationship_history)")
        columns = [r["name"] for r in rows]
        assert "observed_at" in columns

    def test_history_expired_at_column_exists(self):
        """graph_relationship_history 表应包含 expired_at 列。"""
        db = get_db_client()
        rows = db.execute("PRAGMA table_info(graph_relationship_history)")
        columns = [r["name"] for r in rows]
        assert "expired_at" in columns


# ============================================================
# 2. 写入测试：add_relationship / deactivate_relationship
# ============================================================

@pytest.mark.unit
class TestDualTemporalWrite:
    """验证写入时双时序字段被正确设置。"""

    def test_add_relationship_sets_observed_at(self):
        """创建关系时应写入 observed_at。"""
        result = add_relationship(
            user_id=TEST_USER_ID,
            source_name="Alice",
            target_name="AcmeCorp",
            relation_type="colleague",
            valid_from="2026-01-01T00:00:00",
        )
        assert result["success"] is True
        assert result["created"] is True
        assert result["observed_at"] is not None

        db = get_db_client()
        rows = db.execute(
            "SELECT observed_at, expired_at FROM graph_relationships WHERE id = ?",
            (result["relationship_id"],)
        )
        assert rows[0]["observed_at"] is not None
        assert rows[0]["expired_at"] is None  # 新关系未过期

    def test_deactivate_sets_expired_at_and_valid_to(self):
        """deactivate 应同时设置 expired_at（系统时间）和 valid_to（事件时间）。"""
        create = add_relationship(
            user_id=TEST_USER_ID,
            source_name="Bob",
            target_name="BetaCorp",
            relation_type="colleague",
            valid_from="2026-01-01T00:00:00",
        )
        rel_id = create["relationship_id"]

        ended_at = "2026-06-30T00:00:00"
        result = deactivate_relationship(
            user_id=TEST_USER_ID,
            relationship_id=rel_id,
            reason="resigned",
            ended_at=ended_at,
        )
        assert result["success"] is True
        assert result["valid_to"] == ended_at
        assert result["expired_at"] is not None

        db = get_db_client()
        rows = db.execute(
            "SELECT valid_to, expired_at, is_active FROM graph_relationships WHERE id = ?",
            (rel_id,)
        )
        assert rows[0]["valid_to"] == ended_at
        assert rows[0]["expired_at"] is not None
        assert rows[0]["is_active"] == 0


# ============================================================
# 3. 时序点查询测试（Service 层）
# ============================================================

@pytest.mark.unit
class TestGetRelationshipAtTime:
    """get_relationship_at_time 时序点查询核心逻辑。"""

    def _setup_scenario(self):
        """创建测试场景：
        - Alice 2026-01-01 入职 AcmeCorp（valid_from=2026-01-01）
        - Alice 2026-06-30 离职（valid_to=2026-06-30）
        """
        create = add_relationship(
            user_id=TEST_USER_ID,
            source_name="Alice",
            target_name="AcmeCorp",
            relation_type="colleague",
            valid_from="2026-01-01T00:00:00",
        )
        deactivate_relationship(
            user_id=TEST_USER_ID,
            relationship_id=create["relationship_id"],
            reason="resigned",
            ended_at="2026-06-30T00:00:00",
        )
        return create["relationship_id"]

    def test_event_mode_active_period(self):
        """事件模式：查询关系生效期间应返回该关系。"""
        self._setup_scenario()
        result = get_relationship_at_time(
            user_id=TEST_USER_ID,
            at_time="2026-03-15T00:00:00",
            entity_name="Alice",
            time_mode="event",
        )
        assert result["success"] is True
        assert result["count"] == 1
        rel = result["relationships"][0]
        assert rel["source_name"] == "Alice"
        assert rel["target_name"] == "AcmeCorp"
        assert rel["relation_type"] == "colleague"

    def test_event_mode_before_start(self):
        """事件模式：查询关系开始前应返回空。"""
        self._setup_scenario()
        result = get_relationship_at_time(
            user_id=TEST_USER_ID,
            at_time="2025-12-31T00:00:00",
            entity_name="Alice",
            time_mode="event",
        )
        assert result["success"] is True
        assert result["count"] == 0

    def test_event_mode_after_end(self):
        """事件模式：查询关系结束后应返回空。"""
        self._setup_scenario()
        result = get_relationship_at_time(
            user_id=TEST_USER_ID,
            at_time="2026-07-01T00:00:00",
            entity_name="Alice",
            time_mode="event",
        )
        assert result["success"] is True
        assert result["count"] == 0

    def test_event_mode_at_boundary_start(self):
        """事件模式：valid_from 边界值（含等于）应返回该关系。"""
        self._setup_scenario()
        result = get_relationship_at_time(
            user_id=TEST_USER_ID,
            at_time="2026-01-01T00:00:00",
            entity_name="Alice",
            time_mode="event",
        )
        assert result["count"] == 1

    def test_event_mode_at_boundary_end(self):
        """事件模式：valid_to 边界值（不含等于，严格大于）应返回空。"""
        self._setup_scenario()
        # valid_to = 2026-06-30，查询 2026-06-30 时 valid_to > at_time 为 False
        result = get_relationship_at_time(
            user_id=TEST_USER_ID,
            at_time="2026-06-30T00:00:00",
            entity_name="Alice",
            time_mode="event",
        )
        assert result["count"] == 0

    def test_active_relationship_no_valid_to(self):
        """未结束的关系（valid_to=NULL）在生效后任意时间点应返回。"""
        add_relationship(
            user_id=TEST_USER_ID,
            source_name="Charlie",
            target_name="GammaCorp",
            relation_type="colleague",
            valid_from="2026-01-01T00:00:00",
        )
        result = get_relationship_at_time(
            user_id=TEST_USER_ID,
            at_time="2026-12-31T00:00:00",
            entity_name="Charlie",
            time_mode="event",
        )
        assert result["count"] == 1

    def test_system_mode_query(self):
        """系统模式：基于 observed_at / expired_at 查询。"""
        self._setup_scenario()
        # expired_at 是 deactivate 调用时的系统时间（now），查询未来时间应不返回
        future = (datetime.now() + timedelta(days=1)).isoformat()
        result = get_relationship_at_time(
            user_id=TEST_USER_ID,
            at_time=future,
            entity_name="Alice",
            time_mode="system",
        )
        assert result["success"] is True
        # 关系已 expired，系统时间在未来查询时应返回空
        assert result["count"] == 0

    def test_system_mode_before_observed(self):
        """系统模式：在 observed_at 之前查询应返回空。"""
        add_relationship(
            user_id=TEST_USER_ID,
            source_name="Dave",
            target_name="DeltaCorp",
            relation_type="colleague",
            valid_from="2026-01-01T00:00:00",
        )
        past = "2020-01-01T00:00:00"
        result = get_relationship_at_time(
            user_id=TEST_USER_ID,
            at_time=past,
            entity_name="Dave",
            time_mode="system",
        )
        assert result["count"] == 0

    def test_relation_type_filter(self):
        """关系类型过滤应生效。"""
        add_relationship(
            user_id=TEST_USER_ID,
            source_name="Eve",
            target_name="EpsilonCorp",
            relation_type="colleague",
            valid_from="2026-01-01T00:00:00",
        )
        result = get_relationship_at_time(
            user_id=TEST_USER_ID,
            at_time="2026-06-01T00:00:00",
            entity_name="Eve",
            relation_type="friend",  # 不存在的类型
            time_mode="event",
        )
        assert result["count"] == 0

    def test_query_all_entities(self):
        """entity_name 为空时应返回所有实体的关系。"""
        add_relationship(
            user_id=TEST_USER_ID,
            source_name="Frank",
            target_name="ZetaCorp",
            relation_type="colleague",
            valid_from="2026-01-01T00:00:00",
        )
        result = get_relationship_at_time(
            user_id=TEST_USER_ID,
            at_time="2026-06-01T00:00:00",
            time_mode="event",
        )
        assert result["success"] is True
        assert result["count"] >= 1


# ============================================================
# 4. API 端点测试
# ============================================================

@pytest.mark.integration
class TestTemporalAPI:
    """GET /api/v1/memory/graph/temporal 端点测试。"""

    def test_api_temporal_query(self, client, auth_headers):
        """API 应返回时序点查询结果。"""
        # 先创建一个关系
        client.post(
            "/api/v1/memory/graph/relationships",
            json={
                "source_name": "Grace",
                "target_name": "EtaCorp",
                "relation_type": "colleague",
                "valid_from": "2026-01-01T00:00:00",
            },
            headers=auth_headers,
        )

        resp = client.get(
            "/api/v1/memory/graph/temporal",
            params={
                "at": "2026-06-01T00:00:00",
                "entity": "Grace",
                "time_mode": "event",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["count"] >= 1
        assert data["time_mode"] == "event"

    def test_api_temporal_invalid_time_mode(self, client, auth_headers):
        """非法 time_mode 应返回 422。"""
        resp = client.get(
            "/api/v1/memory/graph/temporal",
            params={
                "at": "2026-06-01T00:00:00",
                "time_mode": "invalid",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 422

    def test_api_temporal_missing_at(self, client, auth_headers):
        """缺少 at 参数应返回 422。"""
        resp = client.get(
            "/api/v1/memory/graph/temporal",
            params={"entity": "Grace"},
            headers=auth_headers,
        )
        assert resp.status_code == 422

    def test_api_temporal_with_relation_type(self, client, auth_headers):
        """API 应支持 relation_type 过滤。"""
        client.post(
            "/api/v1/memory/graph/relationships",
            json={
                "source_name": "Heidi",
                "target_name": "ThetaCorp",
                "relation_type": "同事",
                "valid_from": "2026-01-01T00:00:00",
            },
            headers=auth_headers,
        )

        resp = client.get(
            "/api/v1/memory/graph/temporal",
            params={
                "at": "2026-06-01T00:00:00",
                "entity": "Heidi",
                "relation_type": "colleague",
                "time_mode": "event",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] >= 1
