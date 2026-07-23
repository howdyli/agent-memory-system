"""
智能遗忘机制测试（R-07）

验证内容：
1. 多因子重要性评分（compute_importance_score）
2. 批量重要性重算（recalculate_importance）
3. 自动遗忘低价值记忆
4. 重要性分解查询（get_importance_breakdown）
5. 遗忘统计（get_forgetting_statistics）
6. API 端点
7. 维护任务集成（run_maintenance_now）
"""
import pytest
from datetime import datetime, timedelta

from app.core.db_client import get_db_client
from app.services.smart_forgetting_service import (
    compute_importance_score,
    recalculate_importance,
    get_importance_breakdown,
    get_forgetting_statistics,
    DEFAULT_WEIGHTS,
    DEFAULT_FORGET_THRESHOLD,
)
from app.services.memory_fragment_service import create_fragment
from app.services.memory_observability_service import record_trace_event

TEST_USER_ID = 999


@pytest.fixture(autouse=True)
def _clean_tables():
    """每个测试前后清理测试数据。"""
    db = get_db_client()
    try:
        db.execute("DELETE FROM memory_trace_events WHERE user_id = ?", (TEST_USER_ID,))
        db.execute("DELETE FROM memory_fragments WHERE user_id = ?", (TEST_USER_ID,))
    except Exception:
        pass
    yield
    try:
        db.execute("DELETE FROM memory_trace_events WHERE user_id = ?", (TEST_USER_ID,))
        db.execute("DELETE FROM memory_fragments WHERE user_id = ?", (TEST_USER_ID,))
    except Exception:
        pass


# ============================================================
# 1. 单条记忆重要性评分
# ============================================================

@pytest.mark.unit
class TestComputeImportanceScore:
    """多因子重要性评分测试。"""

    def test_basic_score_calculation(self):
        """基本评分计算应返回 0-1 之间的值。"""
        fragment = {
            "fragment_type": "info",
            "created_at": datetime.now().isoformat(),
            "importance_score": 0.8,
        }
        result = compute_importance_score(
            fragment=fragment,
            recall_count=5,
            is_superseded=False,
        )
        assert 0.0 <= result["total_score"] <= 1.0
        assert "factors" in result
        assert "weights" in result

    def test_superseded_memory_gets_penalty(self):
        """被 superseded 的记忆应获得更低的分数。"""
        fragment = {
            "fragment_type": "info",
            "created_at": datetime.now().isoformat(),
            "importance_score": 0.8,
        }
        active_score = compute_importance_score(
            fragment=fragment, recall_count=5, is_superseded=False,
        )
        superseded_score = compute_importance_score(
            fragment=fragment, recall_count=5, is_superseded=True,
        )
        assert superseded_score["total_score"] < active_score["total_score"]

    def test_higher_recall_higher_score(self):
        """召回次数越多，重要性应越高。"""
        fragment = {
            "fragment_type": "info",
            "created_at": datetime.now().isoformat(),
            "importance_score": 0.5,
        }
        low_recall = compute_importance_score(fragment, recall_count=0)
        high_recall = compute_importance_score(fragment, recall_count=20)
        assert high_recall["total_score"] > low_recall["total_score"]
        assert high_recall["factors"]["recall_frequency"] > low_recall["factors"]["recall_frequency"]

    def test_older_memory_lower_decay(self):
        """更老的记忆（非永久类型）时间衰减因子应更低。"""
        recent_fragment = {
            "fragment_type": "preference",  # 半衰期 1 天
            "created_at": datetime.now().isoformat(),
            "importance_score": 0.5,
        }
        old_fragment = {
            "fragment_type": "preference",
            "created_at": (datetime.now() - timedelta(days=30)).isoformat(),
            "importance_score": 0.5,
        }
        recent_score = compute_importance_score(recent_fragment, recall_count=0)
        old_score = compute_importance_score(old_fragment, recall_count=0)
        assert recent_score["factors"]["time_decay"] > old_score["factors"]["time_decay"]

    def test_permanent_type_no_decay(self):
        """永久类型（info）的记忆不应衰减。"""
        fragment = {
            "fragment_type": "info",  # 半衰期 None = 永久
            "created_at": (datetime.now() - timedelta(days=365)).isoformat(),
            "importance_score": 0.5,
        }
        result = compute_importance_score(fragment, recall_count=0)
        assert result["factors"]["time_decay"] == 1.0

    def test_custom_weights(self):
        """自定义权重应生效。"""
        fragment = {
            "fragment_type": "info",
            "created_at": datetime.now().isoformat(),
            "importance_score": 0.5,
        }
        custom_weights = {
            "recall_frequency": 0.5,
            "time_decay": 0.1,
            "evidence": 0.2,
            "contradiction": 0.2,
        }
        result = compute_importance_score(
            fragment, recall_count=5, weights=custom_weights,
        )
        assert result["weights"] == custom_weights

    def test_all_factors_sum_to_weights(self):
        """评分应等于各因子加权和。"""
        fragment = {
            "fragment_type": "info",
            "created_at": datetime.now().isoformat(),
            "importance_score": 0.7,
        }
        result = compute_importance_score(fragment, recall_count=3)
        factors = result["factors"]
        weights = result["weights"]
        expected = (
            weights["recall_frequency"] * factors["recall_frequency"]
            + weights["time_decay"] * factors["time_decay"]
            + weights["evidence"] * factors["evidence"]
            + weights["contradiction"] * factors["contradiction"]
        )
        assert abs(result["total_score"] - round(expected, 4)) < 0.01


# ============================================================
# 2. 批量重要性重算
# ============================================================

@pytest.mark.unit
class TestRecalculateImportance:
    """批量重要性重算测试。"""

    def test_recalculate_empty(self):
        """无记忆时应返回 0 评估。"""
        result = recalculate_importance(user_id=TEST_USER_ID)
        assert result["success"] is True
        assert result["total_evaluated"] == 0

    def test_recalculate_updates_scores(self):
        """重算后 importance_score 应被更新。"""
        # 创建低重要性的记忆
        frag = create_fragment(
            user_id=TEST_USER_ID,
            fragment_type="info",
            content="Test memory for recalculation",
            importance_score=0.3,
        )
        frag_id = frag["fragment_id"]

        result = recalculate_importance(user_id=TEST_USER_ID, auto_forget=False)
        assert result["success"] is True
        assert result["total_evaluated"] >= 1

        # 验证分数已更新
        db = get_db_client()
        rows = db.execute(
            "SELECT importance_score FROM memory_fragments WHERE id = ?",
            (frag_id,),
        )
        assert rows[0]["importance_score"] is not None

    def test_auto_forget_low_importance(self):
        """低重要性记忆应被自动标记为冷记忆。"""
        # 创建一个低重要性、无召回的过期偏好记忆
        db = get_db_client()
        old_time = (datetime.now() - timedelta(days=60)).isoformat()
        db.execute(
            """INSERT INTO memory_fragments
               (user_id, fragment_type, content, importance_score, created_at, lifecycle_status)
               VALUES (?, ?, ?, ?, ?, 'active')""",
            (TEST_USER_ID, "preference", "Old unused preference", 0.1, old_time),
        )

        result = recalculate_importance(
            user_id=TEST_USER_ID,
            auto_forget=True,
            forget_threshold=0.3,
        )
        assert result["success"] is True
        assert result["total_forgotten"] >= 1

    def test_no_auto_forget_when_disabled(self):
        """auto_forget=False 时不应标记冷记忆。"""
        create_fragment(
            user_id=TEST_USER_ID,
            fragment_type="info",
            content="Test no auto forget",
            importance_score=0.1,
        )

        result = recalculate_importance(
            user_id=TEST_USER_ID,
            auto_forget=False,
        )
        assert result["success"] is True
        assert result["total_forgotten"] == 0

    def test_score_distribution_returned(self):
        """应返回评分分布。"""
        create_fragment(
            user_id=TEST_USER_ID,
            fragment_type="info",
            content="Test distribution",
            importance_score=0.5,
        )

        result = recalculate_importance(user_id=TEST_USER_ID, auto_forget=False)
        assert "score_distribution" in result
        assert isinstance(result["score_distribution"], dict)


# ============================================================
# 3. 重要性分解查询
# ============================================================

@pytest.mark.unit
class TestImportanceBreakdown:
    """重要性分解查询测试。"""

    def test_get_breakdown_for_fragment(self):
        """应返回记忆的重要性因子分解。"""
        frag = create_fragment(
            user_id=TEST_USER_ID,
            fragment_type="info",
            content="Test breakdown query",
            importance_score=0.6,
        )

        result = get_importance_breakdown(
            user_id=TEST_USER_ID,
            fragment_id=frag["fragment_id"],
        )
        assert result["success"] is True
        assert result["fragment_id"] == frag["fragment_id"]
        assert "factors" in result
        assert "recall_count" in result
        assert "is_superseded" in result
        assert "current_score" in result
        assert "computed_score" in result

    def test_get_breakdown_not_found(self):
        """不存在的记忆应返回错误。"""
        result = get_importance_breakdown(
            user_id=TEST_USER_ID,
            fragment_id=999999,
        )
        assert result["success"] is False

    def test_breakdown_with_recall_events(self):
        """有召回事件的记忆应反映在 recall_count 中。"""
        frag = create_fragment(
            user_id=TEST_USER_ID,
            fragment_type="info",
            content="Test recall count",
            importance_score=0.5,
        )
        frag_id = frag["fragment_id"]

        # 记录 3 次召回事件
        for _ in range(3):
            record_trace_event(TEST_USER_ID, str(frag_id), "fragment", "recalled", "test")

        result = get_importance_breakdown(
            user_id=TEST_USER_ID,
            fragment_id=frag_id,
        )
        assert result["success"] is True
        assert result["recall_count"] == 3


# ============================================================
# 4. 遗忘统计
# ============================================================

@pytest.mark.unit
class TestForgettingStatistics:
    """遗忘统计测试。"""

    def test_statistics_returned(self):
        """应返回统计信息。"""
        create_fragment(
            user_id=TEST_USER_ID,
            fragment_type="info",
            content="Test stats",
            importance_score=0.5,
        )

        result = get_forgetting_statistics(user_id=TEST_USER_ID)
        assert result["success"] is True
        assert "status_distribution" in result
        assert "score_distribution" in result
        assert "weights" in result
        assert "forget_threshold" in result

    def test_statistics_include_status_counts(self):
        """统计应包含各生命周期状态的数量。"""
        create_fragment(
            user_id=TEST_USER_ID,
            fragment_type="info",
            content="Active memory stats",
            importance_score=0.5,
        )

        result = get_forgetting_statistics(user_id=TEST_USER_ID)
        assert "active" in result["status_distribution"]


# ============================================================
# 5. API 端点测试
# ============================================================

@pytest.mark.integration
class TestSmartForgettingAPI:
    """智能遗忘 API 端点测试。"""

    def test_api_recalculate(self, client, auth_headers):
        """POST /memory/forgetting/recalculate 应触发重算。"""
        client.post(
            "/api/v1/memory/fragments",
            json={"fragment_type": "info", "content": "API recalc test"},
            headers=auth_headers,
        )

        resp = client.post(
            "/api/v1/memory/forgetting/recalculate",
            params={"auto_forget": False},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["total_evaluated"] >= 1

    def test_api_get_importance(self, client, auth_headers):
        """GET /memory/forgetting/importance/{id} 应返回因子分解。"""
        create_resp = client.post(
            "/api/v1/memory/fragments",
            json={"fragment_type": "info", "content": "API importance test"},
            headers=auth_headers,
        )
        frag_id = create_resp.json()["fragment_id"]

        resp = client.get(
            f"/api/v1/memory/forgetting/importance/{frag_id}",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "factors" in data

    def test_api_get_statistics(self, client, auth_headers):
        """GET /memory/forgetting/statistics 应返回统计。"""
        resp = client.get(
            "/api/v1/memory/forgetting/statistics",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True

    def test_api_get_config(self, client, auth_headers):
        """GET /memory/forgetting/config 应返回配置。"""
        resp = client.get(
            "/api/v1/memory/forgetting/config",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "weights" in data
        assert "formula" in data

    def test_api_importance_not_found(self, client, auth_headers):
        """不存在的记忆应返回 404。"""
        resp = client.get(
            "/api/v1/memory/forgetting/importance/999999",
            headers=auth_headers,
        )
        assert resp.status_code == 404

    def test_api_invalid_threshold(self, client, auth_headers):
        """超出范围的阈值应返回 422。"""
        resp = client.post(
            "/api/v1/memory/forgetting/recalculate",
            params={"forget_threshold": 2.0},
            headers=auth_headers,
        )
        assert resp.status_code == 422


# ============================================================
# 6. 维护任务集成测试
# ============================================================

@pytest.mark.integration
class TestMaintenanceIntegration:
    """验证智能遗忘已集成到维护任务中。"""

    def test_run_maintenance_includes_forgetting(self):
        """run_maintenance_now 的结果应包含 smart_forgetting 字段。"""
        from app.services.memory_lifecycle_service import run_maintenance_now

        result = run_maintenance_now()
        assert "smart_forgetting" in result
        assert result["smart_forgetting"]["success"] is True
