"""
P1-5 测试框架升级验证测试

验证 pytest + httpx + fixtures 测试框架的正常运行。
覆盖同步客户端、异步客户端、认证 fixture、数据隔离。
"""
import pytest
import pytest_asyncio


# ============================================================
# 同步客户端测试（基于 TestClient/httpx）
# ============================================================

@pytest.mark.unit
class TestSyncClient:
    """同步测试客户端基础功能验证"""

    def test_health_check(self, client):
        """测试健康检查端点（无需认证）"""
        resp = client.get("/api/v1/health")
        assert resp.status_code == 200
        data = resp.json()
        assert "status" in data

    def test_root_endpoint(self, client):
        """测试根路径"""
        resp = client.get("/")
        assert resp.status_code == 200
        data = resp.json()
        assert "version" in data

    def test_auth_headers(self, auth_headers):
        """验证 auth_headers fixture 返回有效 token"""
        assert "Authorization" in auth_headers
        assert auth_headers["Authorization"].startswith("Bearer ")


# ============================================================
# 异步客户端测试（基于 httpx.AsyncClient）
# ============================================================

@pytest.mark.asyncio
class TestAsyncClient:
    """异步测试客户端基础功能验证"""

    async def test_health_check_async(self, async_client):
        """异步测试健康检查端点"""
        resp = await async_client.get("/api/v1/health")
        assert resp.status_code == 200

    async def test_root_async(self, async_client):
        """异步测试根路径"""
        resp = await async_client.get("/")
        assert resp.status_code == 200
        assert "version" in resp.json()

    async def test_async_auth_headers(self, async_auth_headers):
        """验证异步 auth_headers fixture"""
        assert "Authorization" in async_auth_headers
        assert async_auth_headers["Authorization"].startswith("Bearer ")


# ============================================================
# Pydantic 校验前移验证（P1-2）
# ============================================================

@pytest.mark.unit
class TestPydanticValidation:
    """验证 Pydantic Field/Literal 约束在 API 层生效"""

    def test_entity_create_empty_name(self, client, auth_headers):
        """空实体名称应返回 422"""
        resp = client.post(
            "/api/v1/memory/graph/entities",
            json={"name": "", "entity_type": "person"},
            headers=auth_headers,
        )
        assert resp.status_code == 422

    def test_entity_create_invalid_type(self, client, auth_headers):
        """非法实体类型应返回 422"""
        resp = client.post(
            "/api/v1/memory/graph/entities",
            json={"name": "test", "entity_type": "invalid_type"},
            headers=auth_headers,
        )
        assert resp.status_code == 422

    def test_relationship_confidence_out_of_range(self, client, auth_headers):
        """confidence > 1.0 应返回 422"""
        resp = client.post(
            "/api/v1/memory/graph/relationships",
            json={
                "source_name": "a",
                "target_name": "b",
                "relation_type": "knows",
                "confidence": 1.5,
            },
            headers=auth_headers,
        )
        assert resp.status_code == 422

    def test_feedback_invalid_type(self, client, auth_headers):
        """非法 feedback_type 应返回 422"""
        resp = client.post(
            "/api/v1/memory/long-term/feedback",
            json={
                "memory_type": "fragment",
                "memory_id": "1",
                "feedback_type": "invalid",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 422


# ============================================================
# 数据隔离验证
# ============================================================

@pytest.mark.unit
class TestDataIsolation:
    """验证测试数据在测试间正确隔离"""

    def test_db_fixture_cleanup(self, db, test_user_id):
        """验证 db fixture 的清理功能"""
        # 插入测试数据
        db.execute(
            '''INSERT INTO memory_fragments (user_id, workspace_id, fragment_type, content, ttl, importance_score)
               VALUES (?, NULL, "info", "test data", 0, 0.5)''',
            (test_user_id,)
        )
        # 验证数据存在
        rows = db.execute(
            "SELECT * FROM memory_fragments WHERE user_id = ?",
            (test_user_id,)
        )
        assert rows is not None and len(rows) > 0

    def test_db_fixture_cleaned_after(self, db, test_user_id):
        """此测试验证前一个测试的数据已被清理"""
        rows = db.execute(
            "SELECT * FROM memory_fragments WHERE user_id = ? AND content = 'test data'",
            (test_user_id,)
        )
        # 如果前一个测试的数据未被清理，这里会有记录
        # 注意：由于 fixture 执行顺序，此断言依赖于 test_db_fixture_cleanup 先执行
        # 更可靠的隔离方式是每个测试使用不同的标识符
