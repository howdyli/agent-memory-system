"""
记忆 Playground API 测试（W4-F4.3）

验证内容：
1. 衰减模拟（纯计算）：半衰期曲线、永久类型、覆盖参数、有效寿命
2. 注入模拟（只读）：实体抽取、时间推断、矛盾预判、无落库副作用
3. API 端点：认证要求、参数校验、响应结构
"""
from datetime import datetime, timedelta

import pytest

from app.api.playground import (
    _preview_contradictions,
    _simulate_decay,
    _simulate_injection,
)
from app.core.db_client import get_db_client

TEST_USER_ID = 9941


@pytest.fixture(autouse=True)
def _clean_tables():
    """每个测试前后清理测试数据，并确保测试用户存在（满足外键约束）。"""
    db = get_db_client()
    try:
        db.execute(
            "INSERT OR IGNORE INTO users (id, username) VALUES (?, ?)",
            (TEST_USER_ID, f"w4_playground_test_{TEST_USER_ID}"),
        )
    except Exception:
        pass

    def _clean():
        for sql in (
            "DELETE FROM memory_conflicts WHERE user_id = ?",
            "DELETE FROM memory_evolution WHERE user_id = ?",
            "DELETE FROM memory_fragments WHERE user_id = ?",
        ):
            try:
                db.execute(sql, (TEST_USER_ID,))
            except Exception:
                pass

    _clean()
    yield
    _clean()


def _insert_fragment(
    content: str,
    importance: float = 0.5,
    lifecycle: str = "active",
    created_at: datetime = None,
) -> int:
    """直接插入记忆片段（绕过 create_fragment 的自动矛盾检测副作用）。"""
    db = get_db_client()
    return db.execute(
        """INSERT INTO memory_fragments
           (user_id, fragment_type, content, importance_score,
            lifecycle_status, created_at, vector_synced)
           VALUES (?, 'info', ?, ?, ?, ?, 1)""",
        (
            TEST_USER_ID,
            content,
            importance,
            lifecycle,
            (created_at or datetime.now()).isoformat(),
        ),
    )


def _fragment_count() -> int:
    db = get_db_client()
    rows = db.execute(
        "SELECT COUNT(*) AS n FROM memory_fragments WHERE user_id = ?",
        (TEST_USER_ID,),
    )
    return rows[0]["n"]


def _lifecycle_status(fragment_id: int) -> str:
    db = get_db_client()
    rows = db.execute(
        "SELECT lifecycle_status FROM memory_fragments WHERE id = ?", (fragment_id,)
    )
    return rows[0]["lifecycle_status"]


# ============================================================
# 1. 衰减模拟（纯计算）
# ============================================================

@pytest.mark.unit
class TestSimulateDecay:

    def test_permanent_type_never_decays(self):
        """info 类型半衰期为 None → 永久，曲线恒为 1.0。"""
        result = _simulate_decay("info", importance=0.8, days=30, half_life_override=None)
        assert result["is_permanent"] is True
        assert result["half_life_days"] is None
        assert result["effective_life_days"] is None
        assert all(p["decay_score"] == 1.0 for p in result["curve"])

    def test_preference_half_life_curve(self):
        """preference 半衰期 1 天：day1≈0.5、day2≈0.25。"""
        result = _simulate_decay("preference", importance=1.0, days=4, half_life_override=None)
        assert result["is_permanent"] is False
        assert result["half_life_days"] == 1
        by_day = {p["day"]: p["decay_score"] for p in result["curve"]}
        assert by_day[0.0] == pytest.approx(1.0)
        assert by_day[1.0] == pytest.approx(0.5, abs=0.01)
        assert by_day[2.0] == pytest.approx(0.25, abs=0.01)

    def test_half_life_override_takes_precedence(self):
        """显式 half_life_days 覆盖类型默认配置。"""
        result = _simulate_decay("info", importance=0.5, days=20, half_life_override=10.0)
        assert result["is_permanent"] is False
        assert result["half_life_days"] == 10.0
        by_day = {p["day"]: p["decay_score"] for p in result["curve"]}
        assert by_day[10.0] == pytest.approx(0.5, abs=0.01)

    def test_effective_life_is_half_life_times_432(self):
        """5% 阈值 → 有效寿命 ≈ 半衰期 × 4.32。"""
        result = _simulate_decay("plan", importance=0.5, days=180, half_life_override=None)
        assert result["half_life_days"] == 90
        assert result["effective_life_days"] == pytest.approx(90 * 4.32, abs=0.1)

    def test_effective_score_scaled_by_importance(self):
        """effective_score = decay_score × importance。"""
        result = _simulate_decay("plan", importance=0.6, days=30, half_life_override=None)
        for p in result["curve"]:
            assert p["effective_score"] == pytest.approx(p["decay_score"] * 0.6, abs=0.001)

    def test_curve_sampling_points(self):
        """约 60 个采样点（days=180 → 步长 3 → 61 点）。"""
        result = _simulate_decay("plan", importance=0.5, days=180, half_life_override=None)
        assert len(result["curve"]) == 61
        assert result["curve"][0]["day"] == 0.0
        assert result["curve"][-1]["day"] == 180.0


# ============================================================
# 2. 注入模拟（只读）
# ============================================================

@pytest.mark.integration
class TestSimulateInjection:

    def test_entity_extraction(self):
        """含可更新实体的内容 → 返回实体类型与值。"""
        result = _simulate_injection(TEST_USER_ID, "我搬到上海", "info", 0.5)
        assert result["success"] is True
        assert result["entity"] is not None
        assert result["entity"]["entity_type"] == "location"
        assert "上海" in result["entity"]["entity_value"]

    def test_temporal_inference_with_expiry(self):
        """含时间表达的内容 → 推断出 valid_until。"""
        result = _simulate_injection(TEST_USER_ID, "我下周三要去上海出差", "plan", 0.5)
        assert result["temporal"]["has_expiry"] is True
        assert result["temporal"]["valid_until"] is not None

    def test_no_entity_no_contradictions(self):
        """无实体内容 → entity 为 None，矛盾列表为空。"""
        result = _simulate_injection(TEST_USER_ID, "今天天气不错", "info", 0.5)
        assert result["entity"] is None
        assert result["contradictions"] == []

    def test_lifecycle_info_in_response(self):
        """响应包含类型对应的半衰期配置。"""
        result = _simulate_injection(TEST_USER_ID, "今天天气不错", "plan", 0.5)
        assert result["lifecycle"]["half_life_days"] == 90

    def test_contradiction_preview_latest_wins(self):
        """普通重要性冲突 → 预判 latest_wins / superseded_old，且旧记忆保持 active。"""
        old_id = _insert_fragment("我搬到北京", importance=0.5)
        result = _simulate_injection(TEST_USER_ID, "我搬到上海", "info", 0.5)

        assert len(result["contradictions"]) == 1
        c = result["contradictions"][0]
        assert c["old_fragment_id"] == old_id
        assert c["entity_type"] == "location"
        assert c["predicted_strategy"] == "latest_wins"
        assert c["predicted_action"] == "superseded_old"
        assert old_id in result["fragment_preview"]["would_supersede"]
        # 只读验证：旧记忆未被标记 superseded
        assert _lifecycle_status(old_id) == "active"

    def test_contradiction_preview_manual_review(self):
        """双高重要性冲突 → 预判 manual_review / pending_review，且不写 memory_conflicts。"""
        _insert_fragment("我搬到北京", importance=0.95)
        result = _simulate_injection(TEST_USER_ID, "我搬到上海", "info", 0.95)

        assert len(result["contradictions"]) == 1
        c = result["contradictions"][0]
        assert c["predicted_strategy"] == "manual_review"
        assert c["predicted_action"] == "pending_review"

        db = get_db_client()
        rows = db.execute(
            "SELECT COUNT(*) AS n FROM memory_conflicts WHERE user_id = ?",
            (TEST_USER_ID,),
        )
        assert rows[0]["n"] == 0

    def test_injection_is_read_only(self):
        """模拟注入不创建片段、不写演变记录。"""
        _insert_fragment("我搬到北京", importance=0.5)
        before = _fragment_count()
        _simulate_injection(TEST_USER_ID, "我搬到上海", "info", 0.5)
        assert _fragment_count() == before

        db = get_db_client()
        rows = db.execute(
            "SELECT COUNT(*) AS n FROM memory_evolution WHERE user_id = ?",
            (TEST_USER_ID,),
        )
        assert rows[0]["n"] == 0

    def test_preview_skips_superseded_and_same_value(self):
        """已 superseded 的旧记忆与同值记忆均不参与矛盾预判。"""
        _insert_fragment("我搬到北京", importance=0.5, lifecycle="superseded")
        _insert_fragment("我搬到上海", importance=0.5)  # 同值
        contradictions = _preview_contradictions(
            TEST_USER_ID, "我搬到上海", ("location", "上海"), 0.5
        )
        assert contradictions == []


# ============================================================
# 3. API 端点
# ============================================================

@pytest.mark.integration
class TestPlaygroundEndpoints:

    def test_endpoints_require_auth(self, client):
        """未认证请求返回 401/403。"""
        for path, body in (
            ("/api/v1/playground/simulate-injection", {"content": "测试"}),
            ("/api/v1/playground/simulate-recall", {"query": "测试"}),
            ("/api/v1/playground/simulate-decay", {}),
        ):
            resp = client.post(path, json=body)
            assert resp.status_code in (401, 403), path

    def test_simulate_injection_empty_content_400(self, auth_client):
        resp = auth_client.post(
            "/api/v1/playground/simulate-injection", json={"content": "   "}
        )
        assert resp.status_code == 400

    def test_simulate_recall_empty_query_400(self, auth_client):
        resp = auth_client.post(
            "/api/v1/playground/simulate-recall", json={"query": ""}
        )
        assert resp.status_code == 400

    def test_simulate_decay_endpoint(self, auth_client):
        resp = auth_client.post(
            "/api/v1/playground/simulate-decay",
            json={"fragment_type": "plan", "importance": 0.7, "days": 90},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["half_life_days"] == 90
        assert len(data["curve"]) > 0

    def test_simulate_injection_endpoint(self, auth_client):
        resp = auth_client.post(
            "/api/v1/playground/simulate-injection",
            json={"content": "我下周三要去上海出差", "fragment_type": "plan"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["temporal"]["has_expiry"] is True
        assert data["fragment_preview"]["fragment_type"] == "plan"

    def test_simulate_recall_endpoint(self, auth_client):
        resp = auth_client.post(
            "/api/v1/playground/simulate-recall",
            json={"query": "我喜欢什么饮料", "budget_tokens": 1000},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert set(data.keys()) >= {"level1", "level2", "level3", "budget"}
        assert data["budget"]["budget_tokens"] == 1000
        assert 0.0 <= data["budget"]["utilization"] <= 1.0
