"""
矛盾检测与调和引擎测试（R-05 收尾）

验证内容：
1. memory_evolution 演变记录表创建
2. 模式匹配矛盾检测（location/organization/title/status）
3. 语义矛盾检测（基于 ChromaDB 向量相似度）
4. 演变链追溯（get_evolution_chain）
5. 演变历史查询（get_evolution_history）
6. 演变统计（get_evolution_statistics）
7. create_fragment 自动触发矛盾检测
8. API 端点
"""
import pytest

from app.core.db_client import get_db_client
from app.services.contradiction_service import (
    detect_contradiction,
    get_evolution_chain,
    get_evolution_history,
    get_evolution_statistics,
    _ensure_evolution_table,
    _record_evolution,
)
from app.services.memory_fragment_service import create_fragment

TEST_USER_ID = 999


@pytest.fixture(autouse=True)
def _clean_tables():
    """每个测试前后清理测试数据。"""
    db = get_db_client()
    _ensure_evolution_table()
    # 测试前也清理一次，避免上一轮残留
    try:
        db.execute("DELETE FROM memory_evolution WHERE user_id = ?", (TEST_USER_ID,))
        db.execute("DELETE FROM memory_fragments WHERE user_id = ?", (TEST_USER_ID,))
    except Exception:
        pass
    yield
    try:
        db.execute("DELETE FROM memory_evolution WHERE user_id = ?", (TEST_USER_ID,))
        db.execute("DELETE FROM memory_fragments WHERE user_id = ?", (TEST_USER_ID,))
    except Exception:
        pass


# ============================================================
# 1. 表结构测试
# ============================================================

@pytest.mark.unit
class TestEvolutionTable:
    """验证 memory_evolution 表结构。"""

    def test_table_exists(self):
        """memory_evolution 表应存在。"""
        _ensure_evolution_table()
        db = get_db_client()
        rows = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='memory_evolution'"
        )
        assert len(rows) == 1

    def test_table_has_required_columns(self):
        """表应包含所有必要列。"""
        _ensure_evolution_table()
        db = get_db_client()
        rows = db.execute("PRAGMA table_info(memory_evolution)")
        columns = {r["name"] for r in rows}
        required = {
            "id", "user_id", "workspace_id", "entity_type", "entity_key",
            "old_fragment_id", "new_fragment_id", "old_value", "new_value",
            "detection_method", "similarity_score", "change_reason",
            "observed_at", "created_at",
        }
        assert required.issubset(columns), f"缺失列: {required - columns}"


# ============================================================
# 2. 模式匹配矛盾检测
# ============================================================

@pytest.mark.unit
class TestPatternContradiction:
    """模式匹配矛盾检测（location/organization/title/status）。"""

    def test_location_update_detected(self):
        """搬家应检测为知识更新：旧住址被标记为 superseded。"""
        # 先创建旧记忆
        old = create_fragment(
            user_id=TEST_USER_ID,
            fragment_type="info",
            content="I live in New York",
        )
        old_id = old["fragment_id"]

        # 再创建新记忆（搬家）
        result = detect_contradiction(
            user_id=TEST_USER_ID,
            new_content="I moved to San Francisco",
            new_fragment_id=old_id + 1,  # 模拟新片段 ID
        )

        assert result["success"] is True
        assert old_id in result["superseded_ids"]
        assert "pattern" in result["detection_methods"]

        # 验证旧记忆被标记
        db = get_db_client()
        rows = db.execute(
            "SELECT lifecycle_status FROM memory_fragments WHERE id = ?",
            (old_id,),
        )
        assert rows[0]["lifecycle_status"] == "superseded"

    def test_organization_update_detected(self):
        """换公司应检测为知识更新。"""
        old = create_fragment(
            user_id=TEST_USER_ID,
            fragment_type="info",
            content="I work at Google",
        )
        old_id = old["fragment_id"]

        result = detect_contradiction(
            user_id=TEST_USER_ID,
            new_content="I work at Apple",
            new_fragment_id=old_id + 1,
            enable_semantic=False,  # 仅测模式匹配
        )

        assert result["success"] is True
        assert old_id in result["superseded_ids"]

    def test_no_contradiction_same_value(self):
        """相同值不应触发矛盾。"""
        create_fragment(
            user_id=TEST_USER_ID,
            fragment_type="info",
            content="I live in Boston",
        )

        result = detect_contradiction(
            user_id=TEST_USER_ID,
            new_content="I live in Boston",
            enable_semantic=False,
        )

        assert result["success"] is True
        assert len(result["superseded_ids"]) == 0

    def test_no_contradiction_different_type(self):
        """不同类型的更新不应互相矛盾。"""
        create_fragment(
            user_id=TEST_USER_ID,
            fragment_type="info",
            content="I live in Seattle",
        )

        result = detect_contradiction(
            user_id=TEST_USER_ID,
            new_content="I work at Microsoft",
            enable_semantic=False,
        )

        # location 和 organization 是不同类型，不应互相矛盾
        assert result["success"] is True
        assert len(result["superseded_ids"]) == 0

    def test_evolution_record_written(self):
        """检测到矛盾后应写入 memory_evolution 记录。"""
        old = create_fragment(
            user_id=TEST_USER_ID,
            fragment_type="info",
            content="I live in Chicago",
        )

        detect_contradiction(
            user_id=TEST_USER_ID,
            new_content="I moved to Denver",
            new_fragment_id=old["fragment_id"] + 1,
            enable_semantic=False,
        )

        db = get_db_client()
        rows = db.execute(
            "SELECT * FROM memory_evolution WHERE user_id = ? AND entity_type = 'location'",
            (TEST_USER_ID,),
        )
        assert len(rows) >= 1
        assert rows[0]["old_value"] is not None
        assert rows[0]["new_value"] is not None
        assert rows[0]["detection_method"] == "pattern"


# ============================================================
# 3. 演变链追溯
# ============================================================

@pytest.mark.unit
class TestEvolutionChain:
    """演变链追溯功能。"""

    def test_chain_with_multiple_versions(self):
        """多次更新应形成完整演变链。"""
        # v1: 住纽约
        create_fragment(TEST_USER_ID, "info", "I live in New York")
        # v2: 搬到旧金山
        create_fragment(TEST_USER_ID, "info", "I moved to San Francisco")
        # v3: 搬到洛杉矶
        create_fragment(TEST_USER_ID, "info", "I moved to Los Angeles")

        result = get_evolution_chain(
            user_id=TEST_USER_ID,
            entity_type="location",
        )

        assert result["success"] is True
        assert result["total_versions"] >= 2  # 至少 2 次演变
        # 按时间排序
        chain = result["chain"]
        for i in range(1, len(chain)):
            assert chain[i]["observed_at"] >= chain[i - 1]["observed_at"]

    def test_chain_filtered_by_entity_key(self):
        """entity_key 过滤应生效。"""
        create_fragment(TEST_USER_ID, "info", "I live in Miami")

        result = get_evolution_chain(
            user_id=TEST_USER_ID,
            entity_type="location",
            entity_key="nonexistent_key",
        )

        assert result["success"] is True
        assert result["total_versions"] == 0

    def test_evolution_history_for_fragment(self):
        """查询片段的演变历史。"""
        old = create_fragment(TEST_USER_ID, "info", "I live in Portland")
        old_id = old["fragment_id"]

        create_fragment(TEST_USER_ID, "info", "I moved to Austin")

        # 旧片段应有 as_superseded 记录
        result = get_evolution_history(
            user_id=TEST_USER_ID,
            fragment_id=old_id,
        )

        assert result["success"] is True
        assert len(result["as_superseded"]) >= 1

    def test_evolution_statistics(self):
        """演变统计应正确汇总。"""
        create_fragment(TEST_USER_ID, "info", "I live in Phoenix")
        create_fragment(TEST_USER_ID, "info", "I moved to Las Vegas")

        result = get_evolution_statistics(user_id=TEST_USER_ID)

        assert result["success"] is True
        assert result["total_evolutions"] >= 1
        assert "location" in result["by_entity_type"]


# ============================================================
# 4. create_fragment 自动触发矛盾检测
# ============================================================

@pytest.mark.integration
class TestAutoContradictionInCreateFragment:
    """验证 create_fragment 自动触发矛盾检测。"""

    def test_create_fragment_triggers_contradiction(self):
        """创建第二条矛盾记忆时，返回值应包含 contradiction 信息。"""
        # 第一条：住纽约
        create_fragment(TEST_USER_ID, "info", "I live in New York")

        # 第二条：搬到旧金山 → 应触发矛盾检测
        result = create_fragment(TEST_USER_ID, "info", "I moved to San Francisco")

        assert result["success"] is True
        assert "contradiction" in result
        assert len(result["contradiction"]["superseded_ids"]) >= 1

    def test_create_fragment_no_contradiction(self):
        """无矛盾的创建不应返回 contradiction 字段。"""
        result = create_fragment(TEST_USER_ID, "info", "The sky is blue today")

        assert result["success"] is True
        assert "contradiction" not in result


# ============================================================
# 5. API 端点测试
# ============================================================

@pytest.mark.integration
class TestEvolutionAPI:
    """记忆演变 API 端点测试。"""

    def test_api_get_chain(self, client, auth_headers):
        """GET /memory/evolution/chain 应返回演变链。"""
        # 先创建矛盾记忆
        client.post(
            "/api/v1/memory/fragments",
            json={"fragment_type": "info", "content": "I live in Dallas"},
            headers=auth_headers,
        )
        client.post(
            "/api/v1/memory/fragments",
            json={"fragment_type": "info", "content": "I moved to Houston"},
            headers=auth_headers,
        )

        resp = client.get(
            "/api/v1/memory/evolution/chain",
            params={"entity_type": "location"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["total_versions"] >= 1

    def test_api_get_statistics(self, client, auth_headers):
        """GET /memory/evolution/statistics 应返回统计。"""
        resp = client.get(
            "/api/v1/memory/evolution/statistics",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "total_evolutions" in data

    def test_api_detect_contradiction(self, client, auth_headers):
        """POST /memory/evolution/detect 应返回检测结果。"""
        # 先创建一条旧记忆
        client.post(
            "/api/v1/memory/fragments",
            json={"fragment_type": "info", "content": "I live in Seattle"},
            headers=auth_headers,
        )

        resp = client.post(
            "/api/v1/memory/evolution/detect",
            params={"content": "I moved to Portland", "enable_semantic": False},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert len(data["superseded_ids"]) >= 1

    def test_api_get_fragment_history(self, client, auth_headers):
        """GET /memory/evolution/fragment/{id} 应返回片段演变历史。"""
        # 创建旧记忆
        create_resp = client.post(
            "/api/v1/memory/fragments",
            json={"fragment_type": "info", "content": "I live in Boise"},
            headers=auth_headers,
        )
        old_id = create_resp.json()["fragment_id"]

        # 触发矛盾
        client.post(
            "/api/v1/memory/fragments",
            json={"fragment_type": "info", "content": "I moved to Reno"},
            headers=auth_headers,
        )

        resp = client.get(
            f"/api/v1/memory/evolution/fragment/{old_id}",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert len(data["as_superseded"]) >= 1

    def test_api_invalid_entity_type(self, client, auth_headers):
        """非法 entity_type 应返回 422。"""
        resp = client.get(
            "/api/v1/memory/evolution/chain",
            params={"entity_type": "invalid_type"},
            headers=auth_headers,
        )
        assert resp.status_code == 422
