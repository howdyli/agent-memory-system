"""
Pytest fixtures for Agent Memory System tests.

测试框架：pytest + httpx + pytest-asyncio
- 同步测试：使用 `client` fixture (TestClient, 基于 httpx)
- 异步测试：使用 `async_client` fixture (httpx.AsyncClient)
- 数据隔离：每个测试函数使用独立的测试用户 ID 和自动清理

使用示例：
    # 同步测试
    def test_list_sessions(client, auth_headers):
        resp = client.get("/api/v1/agent/sessions", headers=auth_headers)
        assert resp.status_code == 200

    # 异步测试
    @pytest.mark.asyncio
    async def test_create_fragment(async_client, auth_headers):
        resp = await async_client.post(
            "/api/v1/memory/fragments",
            json={"content": "test", "fragment_type": "info"},
            headers=auth_headers,
        )
        assert resp.status_code == 201
"""
import pytest
import pytest_asyncio
import sys
import os

# Add backend to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.core.db_client import get_db_client
from app.core.redis_client import get_redis_client
from app.core.chromadb_client import get_chromadb_client


# ============================================================
# 基础客户端 Fixtures
# ============================================================

@pytest.fixture(scope="session")
def client():
    """FastAPI 同步测试客户端（session 级别，基于 httpx）。"""
    return TestClient(app)


@pytest_asyncio.fixture(scope="session")
async def async_client():
    """FastAPI 异步测试客户端（session 级别，基于 httpx.AsyncClient）。

    适用于测试 SSE 流式接口或需要并发请求的场景。
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ============================================================
# 数据库 & 存储 Fixtures
# ============================================================

@pytest.fixture(scope="function")
def db():
    """数据库客户端，每个测试函数后清理测试数据。"""
    db_client = get_db_client()
    yield db_client
    # 清理测试用户数据
    _cleanup_test_data(db_client)


def _cleanup_test_data(db_client):
    """清理测试用户（ID=999）产生的所有数据"""
    try:
        for table, condition in [
            ("memory_variables", "user_id = 999"),
            ("memory_fragments", "user_id = 999"),
            ("memory_versions", "user_id = 999"),
            ("memory_feedback", "user_id = 999"),
            ("query_logs", "user_id = 999"),
            ("memory_lifecycle", "user_id = 999"),
            ("memory_delete_log", "user_id = 999"),
            ("memory_merge_log", "user_id = 999"),
            ("vector_outbox", "user_id = 999"),
            ("graph_entities", "user_id = 999"),
            ("graph_relationships", "user_id = 999"),
            ("memory_evolution", "user_id = 999"),
            ("extraction_prompts", "user_id = 999"),
            ("auto_recall_config", "user_id = 999"),
            ("auto_recall_stats", "user_id = 999"),
            ("conversation_history", "user_id = 999"),
            ("conversation_summaries", "user_id = 999"),
            ("chat_sessions", "user_id = 999"),
            ("memory_tables", "user_id = 999"),
        ]:
            try:
                db_client.execute(f"DELETE FROM {table} WHERE {condition}")
            except Exception:
                pass  # 表可能不存在

        # 清理动态创建的物理表（memory_999_* 模式）
        try:
            rows = db_client.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'memory_999_%'"
            )
            if rows:
                for row in rows:
                    tbl_name = row["name"] if isinstance(row, dict) else row[0]
                    db_client.execute(f'DROP TABLE IF EXISTS "{tbl_name}"')
        except Exception:
            pass
    except Exception:
        pass


@pytest.fixture(scope="function")
def redis():
    """Redis 客户端。"""
    return get_redis_client()


@pytest.fixture(scope="function")
def chroma():
    """ChromaDB 客户端。"""
    return get_chromadb_client()


# ============================================================
# 全局状态重置（autouse）
# ============================================================

@pytest.fixture(scope="function", autouse=True)
def _reset_rate_limiter():
    """每个测试前重置 RateLimiter 全局单例，避免测试间状态污染。"""
    try:
        from app.services.security_service import get_rate_limiter
        limiter = get_rate_limiter()
        limiter._requests.clear()
    except Exception:
        pass
    yield
    # 测试后也清理
    try:
        from app.services.security_service import get_rate_limiter
        limiter = get_rate_limiter()
        limiter._requests.clear()
    except Exception:
        pass


@pytest.fixture(scope="function", autouse=True)
def _cleanup_event_bus():
    """每个测试后清理 EventBus 的 dispatch tasks，避免 "Task was destroyed but it is pending" 警告。"""
    yield
    try:
        from app.core.event_bus import _event_bus
        if _event_bus is not None:
            import asyncio
            for task in _event_bus._dispatch_tasks.values():
                task.cancel()
            _event_bus._dispatch_tasks.clear()
            _event_bus._subscribers.clear()
    except Exception:
        pass


@pytest.fixture(scope="function", autouse=True)
def _cleanup_test_data_autouse():
    """每个测试函数后自动清理测试数据（autouse），确保测试间数据隔离。

    无论测试是否显式使用 db fixture，都会执行清理。
    """
    yield
    try:
        db_client = get_db_client()
        _cleanup_test_data(db_client)
    except Exception:
        pass


# ============================================================
# 认证 Fixtures
# ============================================================

@pytest.fixture(scope="function")
def test_user():
    """测试用户凭据。"""
    return {
        "username": "testuser",
        "password": "TestPass123!",
        "email": "test@example.com"
    }


@pytest.fixture(scope="function")
def test_user_id():
    """一致的测试用户 ID（用于直接数据库操作）。"""
    return 999


@pytest.fixture(scope="function")
def auth_headers(client, test_user):
    """获取认证头（同步客户端）。

    自动注册并登录测试用户，返回 Bearer token。
    """
    # 注册（如果已存在则忽略）
    client.post("/api/v1/auth/register", json=test_user)
    # 登录
    response = client.post("/api/v1/auth/login", json={
        "username": test_user["username"],
        "password": test_user["password"]
    })
    token = response.json().get("access_token", "")
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture(scope="function")
async def async_auth_headers(async_client, test_user):
    """获取认证头（异步客户端）。

    自动注册并登录测试用户，返回 Bearer token。
    """
    await async_client.post("/api/v1/auth/register", json=test_user)
    response = await async_client.post("/api/v1/auth/login", json={
        "username": test_user["username"],
        "password": test_user["password"]
    })
    token = response.json().get("access_token", "")
    return {"Authorization": f"Bearer {token}"}


# ============================================================
# 认证客户端 Fixtures（带 Authorization 头）
# ============================================================

@pytest.fixture(scope="function")
def auth_client(client, auth_headers):
    """已认证的同步测试客户端（自动带 Authorization 头）。

    用法：直接使用 auth_client.get("/path") 即可，无需手动传 headers。
    """
    # TestClient 不支持 middleware 注入 headers，需手动传
    class _AuthClient:
        def __init__(self, _client, _headers):
            self._client = _client
            self._headers = _headers

        def get(self, url, **kwargs):
            kwargs.setdefault("headers", {}).update(self._headers)
            return self._client.get(url, **kwargs)

        def post(self, url, **kwargs):
            kwargs.setdefault("headers", {}).update(self._headers)
            return self._client.post(url, **kwargs)

        def put(self, url, **kwargs):
            kwargs.setdefault("headers", {}).update(self._headers)
            return self._client.put(url, **kwargs)

        def delete(self, url, **kwargs):
            kwargs.setdefault("headers", {}).update(self._headers)
            return self._client.delete(url, **kwargs)

    return _AuthClient(client, auth_headers)


# ============================================================
# 测试数据 Fixtures
# ============================================================

@pytest.fixture(scope="function")
def sample_fragment():
    """测试记忆片段数据。"""
    return {
        "content": "用户喜欢喝咖啡，每天早上都会喝一杯",
        "fragment_type": "preference",
        "importance_score": 0.8,
    }


@pytest.fixture(scope="function")
def sample_entity():
    """测试实体数据（知识图谱）。"""
    return {
        "name": "张三",
        "entity_type": "person",
        "aliases": ["老张"],
        "metadata": {"department": "工程部"},
    }


@pytest.fixture(scope="function")
def sample_variable():
    """测试记忆变量数据。"""
    return {
        "key": "test_preference",
        "value": "dark_mode",
        "var_type": "string",
    }
