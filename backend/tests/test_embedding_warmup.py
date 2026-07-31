"""
Embedding 启动预热单测（G1：冷启动优化）

覆盖：
- 预热禁用直通（EMBEDDING_WARMUP_ENABLED=False）
- local 模式成功（embed_query 按预热文本条数调用）
- 失败重试（指数退避）后标记 failed，且绝不抛异常
- 超时保护（EMBEDDING_WARMUP_TIMEOUT）触发重试与最终 failed
- default 模式走 chroma 轻量查询 / chroma 为 None 时跳过
- get_warmup_status / is_warmup_ready 语义（failed 视为 ready，允许降级）
- /health/ready 联动：warming_up → 503；completed → 200；failed → 仍 ready
- search_embeddings 降级路径：embed_query 失败时回退 Chroma 内置嵌入，不抛异常

全部使用 mock，不下载真实模型。
"""
import time
from unittest.mock import MagicMock

import pytest

from app.core.config import get_settings
from app.core import embedding_warmup as warmup_module
from app.core.embedding_warmup import (
    _WARMUP_TEXTS,
    _reset_warmup_state_for_tests,
    _update_state,
    get_warmup_status,
    is_warmup_ready,
    warmup_embedding_models,
)


@pytest.fixture(autouse=True)
def _clean_warmup_state(monkeypatch):
    """每个用例前后重置预热状态与 settings 缓存，并缩小重试退避。"""
    get_settings.cache_clear()
    _reset_warmup_state_for_tests()
    monkeypatch.setattr(warmup_module, "_RETRY_BACKOFF_BASE", 0.01)
    yield
    get_settings.cache_clear()
    _reset_warmup_state_for_tests()


def _mock_provider(monkeypatch, provider):
    """mock app.core.embedding.get_embedding_provider（_do_warmup 内延迟 import）。"""
    monkeypatch.setattr("app.core.embedding.get_embedding_provider", lambda: provider)


def _mock_chroma(monkeypatch, chroma):
    monkeypatch.setattr("app.core.chromadb_client.get_chromadb_client", lambda: chroma)


# ===== warmup_embedding_models =====

@pytest.mark.unit
async def test_warmup_disabled_short_circuit(monkeypatch):
    """预热禁用：直接标记 completed 返回 True，不触碰 provider。"""
    monkeypatch.setenv("EMBEDDING_WARMUP_ENABLED", "false")
    get_settings.cache_clear()
    provider = MagicMock()
    _mock_provider(monkeypatch, provider)

    assert await warmup_embedding_models() is True
    assert get_warmup_status()["status"] == "completed"
    provider.embed_query.assert_not_called()


@pytest.mark.unit
async def test_local_mode_success(monkeypatch):
    """local 模式：embed_query 按预热文本条数调用，标记 completed。"""
    provider = MagicMock()
    provider.embed_query.return_value = [0.1, 0.2]
    _mock_provider(monkeypatch, provider)

    assert await warmup_embedding_models() is True
    status = get_warmup_status()
    assert status["status"] == "completed"
    assert status["error"] is None
    assert status["start_time"] is not None and status["end_time"] is not None
    assert provider.embed_query.call_count == len(_WARMUP_TEXTS)


@pytest.mark.unit
async def test_local_mode_retry_then_success(monkeypatch):
    """首次尝试失败、重试成功：最终 completed 且 error 清空。"""
    calls = {"n": 0}

    def _embed(text):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("model load boom")
        return [0.1]

    provider = MagicMock()
    provider.embed_query.side_effect = _embed
    _mock_provider(monkeypatch, provider)

    assert await warmup_embedding_models() is True
    status = get_warmup_status()
    assert status["status"] == "completed"
    assert status["error"] is None
    # 第 1 次尝试失败 1 调用 + 第 2 次尝试成功 2 调用
    assert provider.embed_query.call_count == 1 + len(_WARMUP_TEXTS)


@pytest.mark.unit
async def test_persistent_failure_marks_failed_without_raising(monkeypatch):
    """持续失败：重试 EMBEDDING_WARMUP_RETRIES 次后 failed，返回 False，不抛异常。"""
    monkeypatch.setenv("EMBEDDING_WARMUP_RETRIES", "2")
    get_settings.cache_clear()
    provider = MagicMock()
    provider.embed_query.side_effect = RuntimeError("boom")
    _mock_provider(monkeypatch, provider)

    assert await warmup_embedding_models() is False
    status = get_warmup_status()
    assert status["status"] == "failed"
    assert "boom" in status["error"]
    # 1 次初始尝试 + 2 次重试
    assert provider.embed_query.call_count == 3


@pytest.mark.unit
async def test_timeout_then_failed(monkeypatch):
    """每次尝试超时：wait_for 触发 TimeoutError，重试耗尽后 failed。"""
    monkeypatch.setenv("EMBEDDING_WARMUP_TIMEOUT", "0.05")
    monkeypatch.setenv("EMBEDDING_WARMUP_RETRIES", "1")
    get_settings.cache_clear()

    provider = MagicMock()
    provider.embed_query.side_effect = lambda text: time.sleep(0.3)
    _mock_provider(monkeypatch, provider)

    assert await warmup_embedding_models() is False
    status = get_warmup_status()
    assert status["status"] == "failed"
    assert "TimeoutError" in status["error"]


@pytest.mark.unit
async def test_default_mode_uses_chroma_light_query(monkeypatch):
    """default 模式（provider 为 None）：通过 chroma 轻量查询触发内置模型加载。"""
    _mock_provider(monkeypatch, None)
    chroma = MagicMock()
    chroma.search_embeddings.return_value = []
    _mock_chroma(monkeypatch, chroma)

    assert await warmup_embedding_models() is True
    assert get_warmup_status()["status"] == "completed"
    chroma.search_embeddings.assert_called_once_with("warmup", n_results=1)


@pytest.mark.unit
async def test_default_mode_chroma_none_skips(monkeypatch):
    """default 模式且 chroma 客户端为 None：跳过并标记 completed。"""
    _mock_provider(monkeypatch, None)
    _mock_chroma(monkeypatch, None)

    assert await warmup_embedding_models() is True
    assert get_warmup_status()["status"] == "completed"


# ===== get_warmup_status / is_warmup_ready 语义 =====

@pytest.mark.unit
def test_status_snapshot_is_copy():
    """get_warmup_status 返回快照副本，外部修改不影响内部状态。"""
    snapshot = get_warmup_status()
    snapshot["status"] = "hacked"
    assert get_warmup_status()["status"] == "pending"


@pytest.mark.unit
def test_is_warmup_ready_semantics():
    """pending/warming_up 未就绪；completed/failed 均视为就绪（failed 允许降级）。"""
    assert is_warmup_ready() is False  # pending
    _update_state(status="warming_up")
    assert is_warmup_ready() is False
    _update_state(status="completed")
    assert is_warmup_ready() is True
    _update_state(status="failed")
    assert is_warmup_ready() is True


@pytest.mark.unit
def test_is_warmup_ready_when_disabled(monkeypatch):
    """预热禁用时无论状态如何均视为就绪。"""
    monkeypatch.setenv("EMBEDDING_WARMUP_ENABLED", "false")
    get_settings.cache_clear()
    assert is_warmup_ready() is True  # 状态仍为 pending


# ===== /health/ready 联动 =====

@pytest.mark.unit
def test_readiness_warming_up_returns_503(client):
    _update_state(status="warming_up")
    resp = client.get("/api/v1/health/ready")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "not_ready"
    assert body["checks"]["embedding_warmup"] == "warming_up"


@pytest.mark.unit
def test_readiness_completed_returns_200(client):
    _update_state(status="completed")
    resp = client.get("/api/v1/health/ready")
    assert resp.status_code == 200
    assert resp.json()["checks"]["embedding_warmup"] == "completed"


@pytest.mark.unit
def test_readiness_failed_still_ready(client):
    """failed 记入 checks 但不置 not_ready（允许降级运行，避免永久摘流）。"""
    _update_state(status="failed", error="boom")
    resp = client.get("/api/v1/health/ready")
    assert resp.status_code == 200
    assert "failed" in resp.json()["checks"]["embedding_warmup"]


@pytest.mark.unit
def test_readiness_gate_disabled_reports_only(client, monkeypatch):
    """READINESS_WAIT_FOR_WARMUP=False：仅报告状态，warming_up 不影响 ready。"""
    monkeypatch.setenv("READINESS_WAIT_FOR_WARMUP", "false")
    get_settings.cache_clear()
    _update_state(status="warming_up")
    resp = client.get("/api/v1/health/ready")
    assert resp.status_code == 200
    assert resp.json()["checks"]["embedding_warmup"] == "warming_up"


# ===== search_embeddings 降级路径 =====

@pytest.mark.unit
def test_search_embeddings_falls_back_when_embed_query_fails(monkeypatch):
    """embed_query 失败：不抛异常，回退 query_texts 交给 Chroma 内置嵌入并返回合法结果。"""
    from app.core.chromadb_client import ChromaDBClient

    provider = MagicMock()
    provider.embed_query.side_effect = RuntimeError("model load boom")
    _mock_provider(monkeypatch, provider)

    chroma = ChromaDBClient.__new__(ChromaDBClient)  # 不触发真实初始化
    chroma.collection = MagicMock()
    chroma.collection.query.return_value = {
        "ids": [["doc-1"]],
        "documents": [["用户喜欢咖啡"]],
        "metadatas": [[{"user_id": "999"}]],
        "distances": [[0.2]],
    }

    results = chroma.search_embeddings("咖啡偏好", n_results=1)

    provider.embed_query.assert_called_once_with("咖啡偏好")
    # 回退后以 query_texts 方式查询（不带 query_embeddings）
    query_kwargs = chroma.collection.query.call_args.kwargs
    assert query_kwargs["query_texts"] == ["咖啡偏好"]
    assert "query_embeddings" not in query_kwargs
    assert len(results) == 1
    assert results[0]["id"] == "doc-1"
    assert results[0]["similarity"] == pytest.approx(0.8)
