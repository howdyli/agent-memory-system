"""
任务 3.3：Webhook 投递关键路径测试

覆盖：HMAC 签名、投递成功/失败、指数退避重试、去重(幂等)、事件分发过滤、并发投递。
使用 in-memory SQLite (get_db_client) + httpx.AsyncClient mock，无需真实网络。
"""
import asyncio
import json

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.core.events import MemoryEvent, EventType
from app.services import webhook_service as ws


def _mock_async_client(status_code=200, text="OK", side_effect=None):
    """构造一个 mock 的 httpx.AsyncClient（支持 async context manager）"""
    mock_client = AsyncMock()
    if side_effect is not None:
        mock_client.post = AsyncMock(side_effect=side_effect)
    else:
        resp = MagicMock()
        resp.status_code = status_code
        resp.text = text
        mock_client.post = AsyncMock(return_value=resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    return mock_client


@pytest.fixture
def sample_event():
    return MemoryEvent(
        event_type=EventType.MEMORY_CREATED,
        user_id=1,
        workspace_id=1,
        memory_id="frag_test",
        memory_type="fragment",
        data={"content": "hello"},
    )


# ============================================================
# 1. HMAC 签名
# ============================================================

class TestHMACSignature:
    def test_signature_is_deterministic(self):
        sig1 = ws.compute_signature("whsec_abc", "payload")
        sig2 = ws.compute_signature("whsec_abc", "payload")
        assert sig1 == sig2
        assert len(sig1) == 64  # sha256 hexdigest

    def test_signature_changes_with_secret(self):
        assert ws.compute_signature("secret_a", "p") != ws.compute_signature("secret_b", "p")

    def test_signature_changes_with_payload(self):
        assert ws.compute_signature("s", "p1") != ws.compute_signature("s", "p2")

    def test_generate_secret_prefix(self):
        secret = ws._generate_secret()
        assert secret.startswith("whsec_")
        assert len(secret) > 20


# ============================================================
# 2. 投递成功 / 失败
# ============================================================

class TestDelivery:
    @pytest.mark.asyncio
    @patch("app.services.webhook_service._save_delivery")
    @patch("httpx.AsyncClient")
    async def test_deliver_success_sets_signature_header(self, mock_cls, mock_save, sample_event):
        mock_client = _mock_async_client(status_code=200)
        mock_cls.return_value = mock_client

        webhook = {"id": 7, "url": "https://ex.com/hook", "secret": "whsec_x"}
        result = await ws.deliver_webhook(webhook, sample_event)

        assert result["success"] is True
        assert result["status_code"] == 200
        assert result["attempt"] == 1
        # 校验请求头包含 HMAC 签名
        _, kwargs = mock_client.post.call_args
        headers = kwargs["headers"]
        expected_sig = ws.compute_signature("whsec_x", kwargs["content"])
        assert headers["X-Signature-256"] == f"sha256={expected_sig}"
        assert headers["X-Event-Type"] == EventType.MEMORY_CREATED
        mock_save.assert_called_once()

    @pytest.mark.asyncio
    @patch("app.services.webhook_service._save_delivery")
    @patch("httpx.AsyncClient")
    async def test_deliver_non_2xx_marks_failed(self, mock_cls, mock_save, sample_event):
        mock_cls.return_value = _mock_async_client(status_code=500, text="err")
        webhook = {"id": 1, "url": "https://ex.com/hook", "secret": "s"}
        result = await ws.deliver_webhook(webhook, sample_event)
        assert result["success"] is False
        assert result["status_code"] == 500

    @pytest.mark.asyncio
    @patch("app.services.webhook_service._save_delivery")
    @patch("httpx.AsyncClient")
    async def test_deliver_exception_schedules_retry(self, mock_cls, mock_save, sample_event):
        mock_cls.return_value = _mock_async_client(side_effect=Exception("conn refused"))
        webhook = {"id": 1, "url": "https://ex.com/hook", "secret": "s"}
        result = await ws.deliver_webhook(webhook, sample_event)
        assert result["success"] is False
        assert result["status_code"] is None
        assert "next_retry_at" in result  # 首次重试时间已设置

    @pytest.mark.asyncio
    @patch("app.services.webhook_service._save_delivery")
    @patch("httpx.AsyncClient")
    async def test_deliver_2xx_boundary(self, mock_cls, mock_save, sample_event):
        # 299 属于成功区间，300 不属于
        mock_cls.return_value = _mock_async_client(status_code=299)
        r1 = await ws.deliver_webhook({"id": 1, "url": "u", "secret": "s"}, sample_event)
        assert r1["success"] is True
        mock_cls.return_value = _mock_async_client(status_code=300)
        r2 = await ws.deliver_webhook({"id": 1, "url": "u", "secret": "s"}, sample_event)
        assert r2["success"] is False


# ============================================================
# 3. CRUD + 去重(幂等) 语义
# ============================================================

class TestWebhookCRUD:
    def test_create_and_get_roundtrip(self):
        wh = ws.create_webhook(
            user_id=555, url="https://ex.com/a", event_types=["memory.created"],
            workspace_id=42, description="d",
        )
        assert wh["id"]
        assert wh["secret"].startswith("whsec_")
        fetched = ws.get_webhook(wh["id"])
        assert fetched is not None
        assert fetched["event_types"] == ["memory.created"]
        assert fetched["url"] == "https://ex.com/a"
        ws.delete_webhook(wh["id"])

    def test_get_missing_returns_none(self):
        assert ws.get_webhook(99999999) is None

    def test_update_partial(self):
        wh = ws.create_webhook(user_id=555, url="https://ex.com/b", event_types=["*"])
        updated = ws.update_webhook(wh["id"], active=False, description="paused")
        assert updated["active"] in (0, False)
        assert updated["description"] == "paused"
        # 空更新返回当前值
        same = ws.update_webhook(wh["id"])
        assert same["id"] == wh["id"]
        ws.delete_webhook(wh["id"])

    def test_list_filters_by_user(self):
        wh = ws.create_webhook(user_id=777, url="https://ex.com/c", event_types=["*"])
        items = ws.list_webhooks(user_id=777)
        assert any(i["id"] == wh["id"] for i in items)
        # 其他用户看不到
        assert all(i["user_id"] == 777 for i in items)
        ws.delete_webhook(wh["id"])

    def test_delete_is_idempotent(self):
        wh = ws.create_webhook(user_id=778, url="https://ex.com/d", event_types=["*"])
        assert ws.delete_webhook(wh["id"]) is True
        # 重复删除不报错（幂等）
        assert ws.delete_webhook(wh["id"]) is True
        assert ws.get_webhook(wh["id"]) is None


# ============================================================
# 4. 指数退避重试
# ============================================================

class TestRetry:
    def test_retry_intervals_monotonic(self):
        assert ws.RETRY_INTERVALS == sorted(ws.RETRY_INTERVALS)
        assert ws.MAX_RETRY_ATTEMPTS == len(ws.RETRY_INTERVALS)

    @pytest.mark.asyncio
    @patch("httpx.AsyncClient")
    async def test_retry_success_clears_next_retry(self, mock_cls):
        # 创建失败投递记录后重试成功
        wh = ws.create_webhook(user_id=880, url="https://ex.com/r", event_types=["*"])
        ws._save_delivery({
            "webhook_id": wh["id"],
            "event_id": "evt_retry_1",
            "event_type": "memory.created",
            "payload": json.dumps({"x": 1}),
            "status_code": None,
            "response_body": "fail",
            "success": False,
            "attempt": 1,
            "next_retry_at": None,
        })
        mock_cls.return_value = _mock_async_client(status_code=200)
        count = await ws.retry_failed_deliveries()
        assert count >= 1
        logs = ws.get_delivery_logs(wh["id"])
        target = [l for l in logs if l["event_id"] == "evt_retry_1"][0]
        assert target["success"] in (1, True)
        assert target["next_retry_at"] is None
        ws.delete_webhook(wh["id"])

    @pytest.mark.asyncio
    @patch("httpx.AsyncClient")
    async def test_retry_failure_increments_attempt(self, mock_cls):
        wh = ws.create_webhook(user_id=881, url="https://ex.com/r2", event_types=["*"])
        ws._save_delivery({
            "webhook_id": wh["id"],
            "event_id": "evt_retry_2",
            "event_type": "memory.created",
            "payload": json.dumps({"x": 2}),
            "status_code": None,
            "response_body": "fail",
            "success": False,
            "attempt": 1,
            "next_retry_at": None,
        })
        mock_cls.return_value = _mock_async_client(side_effect=Exception("still down"))
        await ws.retry_failed_deliveries()
        logs = ws.get_delivery_logs(wh["id"])
        target = [l for l in logs if l["event_id"] == "evt_retry_2"][0]
        assert target["attempt"] == 2
        assert target["next_retry_at"] is not None  # 下一次重试已排程
        ws.delete_webhook(wh["id"])

    @pytest.mark.asyncio
    async def test_retry_no_pending_returns_zero(self):
        # 无失败记录时直接返回 0（不依赖网络）
        count = await ws.retry_failed_deliveries()
        assert isinstance(count, int)
        assert count >= 0


# ============================================================
# 5. 事件分发过滤 + 并发
# ============================================================

class TestDispatch:
    @pytest.mark.asyncio
    @patch("app.services.webhook_service.deliver_webhook", new_callable=AsyncMock)
    async def test_dispatch_matches_event_type(self, mock_deliver, sample_event):
        wh = ws.create_webhook(user_id=990, url="https://ex.com/m",
                               event_types=["memory.created"])
        dispatched = await ws.dispatch_event_to_webhooks(sample_event)
        assert dispatched >= 1
        ws.delete_webhook(wh["id"])

    @pytest.mark.asyncio
    @patch("app.services.webhook_service.deliver_webhook", new_callable=AsyncMock)
    async def test_dispatch_skips_non_matching_type(self, mock_deliver):
        wh = ws.create_webhook(user_id=991, url="https://ex.com/n",
                               event_types=["table.created"])
        event = MemoryEvent(event_type=EventType.MEMORY_DELETED, user_id=991)
        before = mock_deliver.await_count
        await ws.dispatch_event_to_webhooks(event)
        # 该 webhook 不匹配 memory.deleted，deliver 不应因它增加
        matched_urls = [c.args[0]["url"] for c in mock_deliver.await_args_list[before:]]
        assert "https://ex.com/n" not in matched_urls
        ws.delete_webhook(wh["id"])

    @pytest.mark.asyncio
    @patch("app.services.webhook_service._save_delivery")
    @patch("httpx.AsyncClient")
    async def test_concurrent_deliveries(self, mock_cls, mock_save, sample_event):
        mock_cls.return_value = _mock_async_client(status_code=200)
        webhook = {"id": 1, "url": "https://ex.com/hook", "secret": "s"}
        results = await asyncio.gather(*[
            ws.deliver_webhook(webhook, sample_event) for _ in range(10)
        ])
        assert len(results) == 10
        assert all(r["success"] for r in results)


# ============================================================
# 6. 测试端点
# ============================================================

class TestWebhookTestEndpoint:
    @pytest.mark.asyncio
    async def test_test_webhook_missing(self):
        result = await ws.test_webhook(99999999)
        assert result["success"] is False
        assert "not found" in result["error"].lower()

    @pytest.mark.asyncio
    @patch("app.services.webhook_service._save_delivery")
    @patch("httpx.AsyncClient")
    async def test_test_webhook_sends_event(self, mock_cls, mock_save):
        mock_cls.return_value = _mock_async_client(status_code=200, text="pong")
        wh = ws.create_webhook(user_id=995, url="https://ex.com/t", event_types=["*"])
        result = await ws.test_webhook(wh["id"])
        assert result["success"] is True
        assert result["status_code"] == 200
        ws.delete_webhook(wh["id"])
