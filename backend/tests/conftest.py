"""
Pytest fixtures for Agent Memory System tests.
"""
import pytest
import sys
import os

# Add backend to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient
from app.main import app
from app.core.db_client import get_db_client, test_db_connection
from app.core.redis_client import get_redis_client
from app.core.chromadb_client import get_chromadb_client


@pytest.fixture(scope="session")
def client():
    """FastAPI test client (session-scoped)."""
    return TestClient(app)


@pytest.fixture(scope="function")
def db():
    """Get database client, clean up test data after each test."""
    client = get_db_client()
    yield client
    # Cleanup test user data
    client.execute("DELETE FROM memory_variables WHERE user_id = 999")
    client.execute("DELETE FROM memory_fragments WHERE user_id = 999")
    client.execute("DELETE FROM memory_versions WHERE user_id = 999")
    client.execute("DELETE FROM memory_feedback WHERE user_id = 999")
    client.execute("DELETE FROM query_logs WHERE user_id = 999")
    client.execute("DELETE FROM index_suggestions")


@pytest.fixture(scope="function")
def redis():
    """Get Redis client."""
    return get_redis_client()


@pytest.fixture(scope="function")
def chroma():
    """Get ChromaDB client."""
    return get_chromadb_client()


@pytest.fixture(scope="function")
def test_user():
    """Test user credentials."""
    return {
        "username": "testuser",
        "password": "TestPass123!",
        "email": "test@example.com"
    }


@pytest.fixture(scope="function")
def auth_headers(client, test_user):
    """Get authenticated headers for test user."""
    # Register
    client.post("/api/v1/auth/register", json=test_user)
    # Login
    response = client.post("/api/v1/auth/login", json={
        "username": test_user["username"],
        "password": test_user["password"]
    })
    token = response.json().get("access_token", "")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="function")
def test_user_id():
    """Consistent test user ID."""
    return 999
