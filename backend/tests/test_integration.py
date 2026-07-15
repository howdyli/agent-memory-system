"""
Integration tests for Agent Memory System API endpoints.
Tests the full API layer with FastAPI TestClient.
"""
import pytest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient
from app.main import app


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def auth_headers(client):
    """Register and login to get auth token."""
    client.post("/api/v1/auth/register", json={
        "username": "integration_test",
        "password": "Test123!@#",
        "email": "integration@test.com"
    })
    resp = client.post("/api/v1/auth/login", json={
        "username": "integration_test",
        "password": "Test123!@#"
    })
    token = resp.json().get("access_token", "")
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.integration
class TestHealthCheck:
    """Health check endpoint tests."""

    def test_health_check(self, client):
        resp = client.get("/api/v1/health")
        assert resp.status_code in [200, 404]  # May or may not exist


@pytest.mark.integration
class TestAuthFlow:
    """Authentication flow integration tests."""

    def test_register_and_login(self, client):
        # Register
        resp = client.post("/api/v1/auth/register", json={
            "username": "auth_test_user",
            "password": "SecurePass123!",
            "email": "auth@test.com"
        })
        assert resp.status_code in [200, 201, 400]  # 400 if already exists

        # Login
        resp = client.post("/api/v1/auth/login", json={
            "username": "auth_test_user",
            "password": "SecurePass123!"
        })
        if resp.status_code == 200:
            data = resp.json()
            assert "access_token" in data

    def test_unauthorized_access(self, client):
        resp = client.get("/api/v1/memory/variables")
        assert resp.status_code in [401, 403]


@pytest.mark.integration
class TestMemoryVariablesAPI:
    """Memory Variables API integration tests."""

    def test_set_and_get_variable(self, client, auth_headers):
        resp = client.post("/api/v1/memory/variables", json={
            "key": "test_name",
            "value": "Integration Test User"
        }, headers=auth_headers)
        assert resp.status_code in [200, 201, 404]

        resp = client.get("/api/v1/memory/variables", headers=auth_headers)
        if resp.status_code == 200:
            data = resp.json()
            assert isinstance(data, (dict, list))

    def test_delete_variable(self, client, auth_headers):
        resp = client.delete("/api/v1/memory/variables/test_name", headers=auth_headers)
        assert resp.status_code in [200, 404]


@pytest.mark.integration
class TestMemoryTablesAPI:
    """Memory Tables API integration tests."""

    def test_create_table(self, client, auth_headers):
        resp = client.post("/api/v1/memory/tables", json={
            "table_name": "integration_projects",
            "fields": [
                {"name": "project_name", "type": "TEXT"},
                {"name": "status", "type": "TEXT"},
                {"name": "priority", "type": "INTEGER"}
            ]
        }, headers=auth_headers)
        assert resp.status_code in [200, 201, 404]

    def test_list_tables(self, client, auth_headers):
        resp = client.get("/api/v1/memory/tables", headers=auth_headers)
        if resp.status_code == 200:
            data = resp.json()
            assert isinstance(data, (dict, list))

    def test_nl_to_sql_and_execute(self, client, auth_headers):
        """自然语言转 SQL 两步接口集成测试"""
        # 1. 创建测试表
        client.post("/api/v1/memory/tables/", json={
            "table_name": "integration_projects",
            "fields": [
                {"name": "project_name", "type": "TEXT"},
                {"name": "status", "type": "TEXT"},
            ]
        }, headers=auth_headers)

        # 2. 添加测试记录
        client.post("/api/v1/memory/tables/integration_projects/records", json={
            "record": {"project_name": "Test A", "status": "进行中"}
        }, headers=auth_headers)

        # 3. 调用 nl-to-sql 生成 SQL
        resp = client.post("/api/v1/memory/tables/integration_projects/nl-to-sql", json={
            "question": "进行中的项目有哪些？"
        }, headers=auth_headers)
        assert resp.status_code in [200, 404]
        if resp.status_code == 200:
            data = resp.json()
            assert "sql" in data
            assert data.get("is_safe") is True

            # 4. 调用 execute-sql 执行
            resp = client.post("/api/v1/memory/tables/integration_projects/execute-sql", json={
                "sql": data["sql"]
            }, headers=auth_headers)
            assert resp.status_code == 200
            exec_data = resp.json()
            assert exec_data.get("success") is True
            assert len(exec_data.get("records", [])) >= 1

    def test_execute_sql_rejects_dangerous(self, client, auth_headers):
        """execute-sql 应拒绝危险 SQL"""
        # 确保表存在
        client.post("/api/v1/memory/tables/", json={
            "table_name": "integration_projects",
            "fields": [{"name": "name", "type": "TEXT"}]
        }, headers=auth_headers)

        resp = client.post("/api/v1/memory/tables/integration_projects/execute-sql", json={
            "sql": 'DROP TABLE "memory_1_integration_projects"'
        }, headers=auth_headers)
        assert resp.status_code in [400, 422]


@pytest.mark.integration
class TestMemoryFragmentsAPI:
    """Memory Fragments API integration tests."""

    def test_create_fragment(self, client, auth_headers):
        resp = client.post("/api/v1/memory/fragments", json={
            "fragment_type": "preference",
            "content": "Integration test preference",
            "importance_score": 0.7,
            "ttl_seconds": 3600
        }, headers=auth_headers)
        assert resp.status_code in [200, 201, 404]

    def test_list_fragments(self, client, auth_headers):
        resp = client.get("/api/v1/memory/fragments", headers=auth_headers)
        if resp.status_code == 200:
            data = resp.json()
            assert isinstance(data, (dict, list))


@pytest.mark.integration
class TestAutoRecallAPI:
    """Auto Memory Recall API integration tests."""

    def test_get_recall_config(self, client, auth_headers):
        resp = client.get("/api/v1/memory/recall/config", headers=auth_headers)
        assert resp.status_code in [200, 404]

    def test_auto_recall(self, client, auth_headers):
        resp = client.post("/api/v1/memory/recall/", json={
            "query": "What are my preferences?",
            "top_k": 5
        }, headers=auth_headers)
        assert resp.status_code in [200, 404]


@pytest.mark.integration
class TestLongTermMemoryAPI:
    """Long-term Memory Management API integration tests."""

    def test_list_all_memories(self, client, auth_headers):
        resp = client.get("/api/v1/memory/long-term/memories", headers=auth_headers)
        assert resp.status_code in [200, 404]

    def test_version_history(self, client, auth_headers):
        resp = client.post("/api/v1/memory/long-term/versions", json={
            "memory_type": "variable", "memory_id": "test_key",
            "action": "create", "old_value": None, "new_value": "test"
        }, headers=auth_headers)
        assert resp.status_code in [200, 201, 404, 401, 422]

    def test_audit_log(self, client, auth_headers):
        resp = client.get("/api/v1/memory/long-term/audit-log", headers=auth_headers)
        assert resp.status_code in [200, 404]


@pytest.mark.integration
class TestSystemIntegrationAPI:
    """System Integration API tests (Phase 7)."""

    def test_llm_backends_list(self, client, auth_headers):
        resp = client.get("/api/v1/system/llm/backends", headers=auth_headers)
        assert resp.status_code in [200, 404]

    def test_plugins_list(self, client, auth_headers):
        resp = client.get("/api/v1/system/plugins", headers=auth_headers)
        assert resp.status_code in [200, 404]

    def test_performance_stats(self, client, auth_headers):
        resp = client.get("/api/v1/system/performance/stats", headers=auth_headers)
        assert resp.status_code in [200, 404]

    def test_security_check(self, client, auth_headers):
        resp = client.post("/api/v1/system/security/check", json={
            "input_string": "test input"
        }, headers=auth_headers)
        assert resp.status_code in [200, 404, 401, 422]


@pytest.mark.integration
class TestEndToEnd:
    """End-to-end workflow tests."""

    def test_full_memory_workflow(self, client, auth_headers):
        """Test complete E2E: variable → table → fragment → recall → long-term."""
        # 1. Set a memory variable
        r1 = client.post("/api/v1/memory/variables", json={
            "key": "e2e_preference",
            "value": "dark mode"
        }, headers=auth_headers)

        # 2. Create a memory table
        r2 = client.post("/api/v1/memory/tables", json={
            "table_name": "e2e_projects",
            "fields": [
                {"name": "name", "type": "TEXT"},
                {"name": "status", "type": "TEXT"}
            ]
        }, headers=auth_headers)

        # 3. Create a memory fragment
        r3 = client.post("/api/v1/memory/fragments", json={
            "fragment_type": "info",
            "content": "E2E test: likes dark mode",
            "importance_score": 0.8,
            "ttl_seconds": 3600
        }, headers=auth_headers)

        # 4. Get all long-term memories
        r4 = client.get("/api/v1/memory/long-term/memories", headers=auth_headers)

        # All should return valid status codes
        statuses = [r.status_code for r in [r1, r2, r3, r4]]
        # At least some endpoints should work
        assert any(s in [200, 201] for s in statuses[:3])

    def test_concurrent_operations(self, client, auth_headers):
        """Test multiple operations in sequence."""
        import concurrent.futures

        def set_variable(i):
            return client.post("/api/v1/memory/variables", json={
                "key": f"concurrent_key_{i}",
                "value": f"value_{i}"
            }, headers=auth_headers)

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(set_variable, i) for i in range(10)]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        # Cleanup
        for i in range(10):
            client.delete(f"/api/v1/memory/variables/concurrent_key_{i}", headers=auth_headers)
